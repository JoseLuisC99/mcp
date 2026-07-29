from __future__ import annotations

import json

import pytest

from oracle.oci_oracle_db_observability.registry import RegistryError, _validate, load_registry
from oracle.oci_oracle_db_observability.registry import to_jsonable


def test_packaged_registry_has_expected_shape() -> None:
    registry = load_registry()

    assert len(registry.skills) == 32
    assert len(registry.tools) == 225
    assert len({tool["name"] for tool in registry.tools}) == 225
    assert all(tool["inputSchema"]["type"] == "object" for tool in registry.tools)
    assert all(tool["inputSchema"].get("additionalProperties") is not True for tool in registry.tools)
    assert "retrieve_data" not in {tool["name"] for tool in registry.tools}


def test_tool_filtering_is_skill_scoped() -> None:
    registry = load_registry()

    tools = registry.list_tools({"awr-historical-performance-analysis"})

    assert tools
    assert all("awr-historical-performance-analysis" in tool["skills"] for tool in tools)


def test_tool_filtering_requires_all_keywords_in_name_or_description() -> None:
    registry = load_registry()

    tools = registry.list_tools(
        {"database-insights"},
        ["resource", "usage"],
    )

    assert tools
    assert all(
        all(
            keyword in f"{tool['name'].replace('_', ' ')} {tool['description']}".casefold()
            for keyword in ("resource", "usage")
        )
        for tool in tools
    )


def test_tool_filtering_rejects_blank_keywords() -> None:
    with pytest.raises(RegistryError, match="Keywords must be non-empty"):
        load_registry().list_tools({"database-insights"}, ["resource", " "])


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

    metadata = files("oracle.oci_oracle_db_observability").joinpath("metadata")
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
