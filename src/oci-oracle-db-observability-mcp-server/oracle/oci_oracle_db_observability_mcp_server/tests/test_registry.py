from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from oracle.oci_oracle_db_observability_mcp_server.registry import RegistryError, _validate, client_class, load_registry
from oracle.oci_oracle_db_observability_mcp_server.registry import to_jsonable
from oracle.oci_oracle_db_observability_mcp_server.sdk_schema import (
    _enum_from_description,
    _model_maps,
    _preserve_constraints,
    _split_pair,
    expected_kwargs,
    schema_for_operation,
    schema_for_type,
)


def test_packaged_registry_has_expected_shape() -> None:
    registry = load_registry()

    assert len(registry.skills) == 34
    assert len(registry.tools) == 229
    assert len({tool["name"] for tool in registry.tools}) == 229
    assert all(tool["inputSchema"]["type"] == "object" for tool in registry.tools)
    assert all(tool["inputSchema"].get("additionalProperties") is False for tool in registry.tools)
    assert "retrieve_data" not in {tool["name"] for tool in registry.tools}
    assert not any(tool["mutable"] for tool in registry.tools)
    assert "query_opsi_data_object_data" not in {tool["name"] for tool in registry.tools}


def test_tool_filtering_is_skill_scoped() -> None:
    registry = load_registry()

    tools = registry.list_tools({"awr-historical-performance-analysis"})

    assert tools
    assert all("awr-historical-performance-analysis" in tool["skills"] for tool in tools)


def test_unknown_skill_and_tool_are_rejected() -> None:
    registry = load_registry()

    with pytest.raises(RegistryError, match="Unknown skill"):
        registry.get_skill("missing")
    with pytest.raises(RegistryError, match="Unknown tool"):
        registry.get_tool("missing")
    with pytest.raises(RegistryError, match="Unknown skill"):
        registry.list_tools({"missing"})


def test_duplicate_names_are_rejected() -> None:
    registry = load_registry()
    duplicate_skill = dict(registry.skills[0])
    duplicate_tool = dict(registry.tools[0])

    with pytest.raises(RegistryError, match="Duplicate skill"):
        _validate([registry.skills[0], duplicate_skill], [])
    with pytest.raises(RegistryError, match="Duplicate tool"):
        _validate(list(registry.skills), [duplicate_tool, duplicate_tool])


def test_metadata_files_are_valid_json() -> None:
    from importlib.resources import files

    metadata = files("oracle.oci_oracle_db_observability_mcp_server").joinpath("metadata")
    for name in ("manifest.json", "skills.json", "tools.json"):
        json.loads(metadata.joinpath(name).read_text(encoding="utf-8"))


def test_compartment_bootstrap_tool_is_not_registry_backed() -> None:
    registry = load_registry()

    assert "list_compartments" not in {tool["name"] for tool in registry.tools}
    assert "list_compartments" not in registry.get_skill("oci-common")["tools"]


def test_registry_values_can_be_returned_as_json() -> None:
    schema = to_jsonable(load_registry().get_tool("summarize_database_insight_resource_usage")["inputSchema"])

    assert isinstance(schema, dict)
    assert json.loads(json.dumps(schema)) == schema


def test_packaged_schemas_match_the_locked_oci_sdk() -> None:
    registry = load_registry()

    for tool in registry.tools:
        method = getattr(client_class(tool["service"], tool["client"]), tool["operation"])
        models = __import__(f"{method.__module__.rsplit('.', 1)[0]}.models", fromlist=["models"])
        schema = to_jsonable(tool["inputSchema"])
        assert schema == schema_for_operation(method, models, schema)


@pytest.mark.parametrize("mutation, error", [(lambda schema: schema.pop("additionalProperties"), "unrestricted object"), (lambda schema: schema["properties"].update({"values": {"type": "array"}}), "untyped array")])
def test_registry_rejects_open_objects_and_untyped_arrays(mutation, error) -> None:
    registry = load_registry()
    tool = to_jsonable(registry.tools[0])
    tool["skills"] = ["skill"]
    mutation(tool["inputSchema"])

    with pytest.raises(RegistryError, match=error):
        _validate([{"name": "skill", "tools": [tool["name"]]}], [tool])


def test_sdk_schema_helpers_reject_unresolvable_contracts() -> None:
    def no_expected_kwargs(self, **kwargs):
        return kwargs

    def mismatched_docs(self, **kwargs):
        """:param str other: (optional) A different argument."""
        expected_kwargs = ["value"]
        return kwargs, expected_kwargs

    assert _split_pair("str, list[int]") == ("str", "list[int]")
    assert _enum_from_description("No SDK enum here") is None
    assert schema_for_type("Any", SimpleNamespace()) == {}
    _preserve_constraints({}, None)
    with pytest.raises(ValueError, match="expected_kwargs"):
        expected_kwargs(no_expected_kwargs)
    with pytest.raises(ValueError, match="does not expose schema maps"):
        _model_maps(object)
    with pytest.raises(ValueError, match="Unable to resolve OCI SDK type"):
        schema_for_type("MissingModel", SimpleNamespace())
    with pytest.raises(ValueError, match="documentation and expected_kwargs differ"):
        schema_for_operation(mismatched_docs, SimpleNamespace())
