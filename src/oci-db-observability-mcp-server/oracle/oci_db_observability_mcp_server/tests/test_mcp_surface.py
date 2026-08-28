"""
Copyright (c) 2026, Oracle and/or its affiliates.
Licensed under the Universal Permissive License v1.0 as shown at
https://oss.oracle.com/licenses/upl.
"""

from __future__ import annotations

import re

import pytest

from oracle.oci_db_observability_mcp_server import mcp as mcp_module
from oracle.oci_db_observability_mcp_server.mcp import mcp


@pytest.mark.asyncio
async def test_unified_mcp_exposes_only_discovery_dispatch_and_compartment_tools() -> None:
    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == {
        "describe_dbo_tool",
        "get_oci_compartment",
        "invoke_dbo_tool",
        "list_oci_compartments",
        "list_dbo_skills",
        "list_dbo_tools",
        "resolve_oci_compartment",
    }


@pytest.mark.asyncio
async def test_advertised_workflow_references_registered_tools_only() -> None:
    registered_names = {tool.name for tool in await mcp.list_tools()}
    advertised_names = set(re.findall(r"`([^`]+)`", mcp.instructions))

    assert advertised_names <= registered_names


def test_compartment_tools_use_the_shared_bootstrap_client(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class Client:
        def get_compartment(self, **kwargs):
            calls["get"] = kwargs
            return object()

        def list_compartments(self, **kwargs):
            calls["list"] = kwargs
            return object()

    monkeypatch.setattr(mcp_module, "identity_bootstrap_client", Client)
    monkeypatch.setattr(
        mcp_module,
        "serialize_response",
        lambda _response: {"data": [{"id": "compartment-ocid"}], "nextPage": "page-2"},
    )

    assert mcp_module.get_oci_compartment("compartment-ocid") == {
        "data": [{"id": "compartment-ocid"}],
        "nextPage": "page-2",
    }
    assert calls["get"] == {"compartment_id": "compartment-ocid"}
    assert mcp_module.list_oci_compartments(
        root_compartment_id="root-compartment-ocid",
        include_subtree=True,
        access_level="ACCESSIBLE",
        name="production",
        lifecycle_state=None,
        limit=10,
        page="page-1",
    ) == {
        "items": [{"id": "compartment-ocid"}],
        "count": 1,
        "nextPage": "page-2",
    }
    assert calls["list"] == {
        "compartment_id": "root-compartment-ocid",
        "compartment_id_in_subtree": True,
        "access_level": "ACCESSIBLE",
        "name": "production",
        "lifecycle_state": None,
        "limit": 10,
        "page": "page-1",
    }


def test_get_compartment_requires_an_ocid() -> None:
    with pytest.raises(ValueError, match="compartment_id is required"):
        mcp_module.get_oci_compartment("")


def test_list_compartments_requires_a_root_ocid_without_a_default(monkeypatch) -> None:
    monkeypatch.delenv("DBO_MCP_TENANCY_IDS", raising=False)
    with pytest.raises(ValueError, match="root_compartment_id is required"):
        mcp_module.list_oci_compartments(
            root_compartment_id="",
            include_subtree=True,
            access_level="ACCESSIBLE",
            name=None,
            lifecycle_state=None,
            limit=50,
            page=None,
        )


def test_list_compartments_uses_a_single_configured_root(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class Client:
        def list_compartments(self, **kwargs):
            calls["list"] = kwargs
            return object()

    monkeypatch.setenv("DBO_MCP_TENANCY_IDS", "configured-root")
    monkeypatch.setattr(mcp_module, "identity_bootstrap_client", Client)
    monkeypatch.setattr(mcp_module, "serialize_response", lambda _response: {"data": [], "nextPage": None})

    assert mcp_module.list_oci_compartments(
        include_subtree=True,
        access_level="ACCESSIBLE",
        name=None,
        lifecycle_state=None,
        limit=50,
        page=None,
    ) == {"items": [], "count": 0, "nextPage": None}
    assert calls["list"] == {
        "compartment_id": "configured-root",
        "compartment_id_in_subtree": True,
        "access_level": "ACCESSIBLE",
        "name": None,
        "lifecycle_state": None,
        "limit": 50,
        "page": None,
    }


def test_resolve_compartment_searches_configured_roots_and_reports_ambiguity(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Response:
        def __init__(self, data, next_page=None):
            self.data = data
            self.headers = {"opc-next-page": next_page} if next_page else {}

    class Client:
        def get_compartment(self, *, compartment_id):
            return Response({"id": compartment_id, "name": f"root-{compartment_id}"})

        def list_compartments(self, **kwargs):
            calls.append(kwargs)
            root = kwargs["compartment_id"]
            if root not in {"root-a", "root-b"}:
                return Response([])
            return Response(
                [
                    {"id": f"{root}-dbanalytics", "name": "dbanalytics", "compartment_id": root},
                ]
            )

    monkeypatch.setenv("DBO_MCP_TENANCY_IDS", "root-a, root-b, root-a")
    monkeypatch.setattr(mcp_module, "identity_bootstrap_client", Client)
    monkeypatch.setattr(
        mcp_module,
        "serialize_response",
        lambda response: {"data": response.data, "nextPage": response.headers.get("opc-next-page")},
    )

    assert mcp_module.resolve_oci_compartment("DBANALYTICS") == {
        "name": "DBANALYTICS",
        "resolution": "ambiguous",
        "matches": [
            {
                "id": "root-a-dbanalytics",
                "name": "dbanalytics",
                "path": "root-root-a/dbanalytics",
                "rootCompartmentId": "root-a",
            },
            {
                "id": "root-b-dbanalytics",
                "name": "dbanalytics",
                "path": "root-root-b/dbanalytics",
                "rootCompartmentId": "root-b",
            },
        ],
    }
    assert [call["compartment_id"] for call in calls] == [
        "root-a",
        "root-a-dbanalytics",
        "root-b",
        "root-b-dbanalytics",
    ]
    assert all("name" not in call for call in calls)


def test_resolve_compartment_requires_a_configured_or_explicit_root(monkeypatch) -> None:
    monkeypatch.delenv("DBO_MCP_TENANCY_IDS", raising=False)
    with pytest.raises(ValueError, match="No compartment discovery root"):
        mcp_module.resolve_oci_compartment("dbanalytics")


def test_discovery_and_invocation_tools_delegate_to_registry(monkeypatch) -> None:
    skills = mcp_module.list_dbo_skills()["skills"]
    assert len(skills) == 35
    assert {"name", "description", "toolCount"}.issubset(skills[0])

    listed = mcp_module.list_dbo_tools(["database-inventory"], limit=1)
    assert listed["count"] >= 1
    assert len(listed["tools"]) == 1
    assert listed["truncated"] is (listed["count"] > 1)
    assert "database-inventory" in listed["tools"][0]["skills"]
    assert "compartmentRequirements" in listed["tools"][0]

    described = mcp_module.describe_dbo_tool("list_database_insights")
    assert described["name"] == "list_database_insights"
    assert described["inputSchema"]["type"] == "object"
    assert described["compartmentRequirements"] == [{"argument": "compartment_id", "required": False}]

    expected = {"result": "ok"}
    monkeypatch.setattr(mcp_module, "invoke_registered_tool", lambda tool, arguments: (tool["name"], arguments, expected))
    assert mcp_module.invoke_dbo_tool("list_database_insights", {"compartment_id": "ocid"}) == (
        "list_database_insights",
        {"compartment_id": "ocid"},
        expected,
    )
