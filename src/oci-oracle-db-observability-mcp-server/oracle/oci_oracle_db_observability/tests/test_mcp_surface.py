from __future__ import annotations

import pytest

from oracle.oci_oracle_db_observability import mcp as mcp_module
from oracle.oci_oracle_db_observability.mcp import mcp


@pytest.mark.asyncio
async def test_unified_mcp_exposes_only_discovery_dispatch_and_compartment_tools() -> None:
    tools = await mcp.list_tools()

    assert {tool.name for tool in tools} == {
        "describe_tool",
        "get_compartment",
        "invoke_tool",
        "list_compartments",
        "list_skills",
        "list_tools",
    }


def test_compartment_tools_use_the_shared_bootstrap_client(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class Client:
        def get_compartment(self, **kwargs):
            calls["get"] = kwargs
            return object()

        def list_compartments(self, **kwargs):
            calls["list"] = kwargs
            return object()

    monkeypatch.setattr(mcp_module, "identity_bootstrap_client", lambda: (Client(), "tenancy-ocid"))
    monkeypatch.setattr(mcp_module, "serialize_response", lambda _response: [{"id": "compartment-ocid"}])

    assert mcp_module.get_compartment("compartment-ocid") == [{"id": "compartment-ocid"}]
    assert calls["get"] == {"compartment_id": "compartment-ocid"}
    assert mcp_module.list_compartments(
        root_compartment_id=None,
        include_subtree=True,
        access_level="ACCESSIBLE",
        name="production",
        lifecycle_state=None,
        limit=10,
    ) == {
        "items": [{"id": "compartment-ocid"}],
        "count": 1,
    }
    assert calls["list"] == {
        "compartment_id": "tenancy-ocid",
        "compartment_id_in_subtree": True,
        "access_level": "ACCESSIBLE",
        "name": "production",
        "lifecycle_state": None,
        "limit": 10,
    }


def test_get_compartment_requires_an_ocid() -> None:
    with pytest.raises(ValueError, match="compartment_id is required"):
        mcp_module.get_compartment("")


def test_discovery_and_invocation_tools_delegate_to_registry(monkeypatch) -> None:
    skills = mcp_module.list_skills()["skills"]
    assert len(skills) == 32
    assert {"name", "description", "toolCount"}.issubset(skills[0])

    listed = mcp_module.list_tools(["database-insights"], ["database", "insights"], limit=1)
    assert listed["count"] >= 1
    assert len(listed["tools"]) == 1
    assert listed["truncated"] is (listed["count"] > 1)
    assert "database-insights" in listed["tools"][0]["skills"]

    described = mcp_module.describe_tool("list_database_insights")
    assert described["name"] == "list_database_insights"
    assert described["inputSchema"]["type"] == "object"

    expected = {"result": "ok"}
    monkeypatch.setattr(mcp_module, "invoke_registered_tool", lambda tool, arguments: (tool["name"], arguments, expected))
    assert mcp_module.invoke_tool("list_database_insights", {"compartment_id": "ocid"}) == (
        "list_database_insights",
        {"compartment_id": "ocid"},
        expected,
    )
