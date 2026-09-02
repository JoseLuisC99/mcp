"""
Copyright (c) 2026, Oracle and/or its affiliates.
Licensed under the Universal Permissive License v1.0 as shown at
https://oss.oracle.com/licenses/upl.
"""

from __future__ import annotations
import os
from typing import Annotated, Any
from fastmcp import FastMCP
from pydantic import Field
from .registry import compartment_requirements, load_registry, to_jsonable
from .runtime import invoke_registered_tool
from .runtime import identity_bootstrap_client, serialize_response

registry = load_registry()


def _configured_compartment_roots() -> tuple[str, ...]:
    """Return the configured, deduplicated compartment discovery roots."""
    values = (value.strip() for value in os.getenv("DBO_MCP_TENANCY_IDS", "").split(","))
    return tuple(dict.fromkeys(value for value in values if value))


def _discovery_roots(root_compartment_ids: list[str] | None) -> tuple[str, ...]:
    """Use explicit roots when supplied, otherwise use server configuration."""
    roots = tuple(dict.fromkeys(root.strip() for root in (root_compartment_ids or []) if root and root.strip()))
    if roots:
        return roots
    roots = _configured_compartment_roots()
    if roots:
        return roots
    raise ValueError(
        "No compartment search scope is configured. To find a compartment by name, configure DBO_MCP_TENANCY_IDS "
        "with one or more tenancy OCIDs and reconnect the MCP, or retry with root_compartment_ids. You can also "
        "provide the compartment OCID directly. The server will not guess because compartment names may not be unique."
    )


def _compartment_path(compartment_id: str, compartments: dict[str, dict[str, Any]], root_id: str) -> str:
    """Build a display-name path without following malformed parent cycles."""
    names: list[str] = []
    current_id: str | None = compartment_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        compartment = compartments.get(current_id)
        if compartment is None:
            break
        names.append(str(compartment.get("name") or current_id))
        if current_id == root_id:
            break
        current_id = compartment.get("compartment_id")
    return "/".join(reversed(names))


def _compartments_under_root(client: Any, root_compartment_id: str) -> dict[str, dict[str, Any]]:
    """List a root and its accessible active descendants for name resolution."""
    root = serialize_response(client.get_compartment(compartment_id=root_compartment_id))["data"]
    compartments: dict[str, dict[str, Any]] = {}
    if isinstance(root, dict) and root.get("id"):
        compartments[str(root["id"])] = root

    pending = [root_compartment_id]
    visited: set[str] = set()
    while pending:
        parent_id = pending.pop(0)
        if parent_id in visited:
            continue
        visited.add(parent_id)
        page: str | None = None
        while True:
            result = serialize_response(
                client.list_compartments(
                    compartment_id=parent_id,
                    access_level="ACCESSIBLE",
                    lifecycle_state="ACTIVE",
                    limit=1000,
                    page=page,
                )
            )
            for compartment in result["data"] if isinstance(result["data"], list) else []:
                if isinstance(compartment, dict) and compartment.get("id"):
                    compartment_id = str(compartment["id"])
                    compartments[compartment_id] = compartment
                    if compartment_id not in visited:
                        pending.append(compartment_id)
            page = result["nextPage"]
            if page is None:
                break
    return compartments


mcp = FastMCP(
    name="oracle.oci-db-observability-mcp-server",
    instructions="For a compartment-scoped operation, use a user-provided compartment OCID, or resolve a user-provided compartment name with "
                 "`resolve_oci_compartment`. It searches DBO_MCP_TENANCY_IDS when configured, or explicit roots supplied to the tool. If no "
                 "discovery root is available, ask the user for one; do not invent an OCID. If resolution is ambiguous, ask the user to choose a returned "
                 "path. Validate an already known OCID with `get_oci_compartment` when scope is not already known. Pass the resolved compartment OCID "
                 "to subsequent Database Observability operations. "
                 "Use `list_dbo_skills` and `list_dbo_tools` when the relevant capability or operation is not already known. "
                 "Use `describe_dbo_tool` to inspect or confirm a tool's exact contract when its schema is unavailable, "
                 "outdated, or uncertain. Reuse information from earlier discovery calls when it remains applicable, then "
                 "call `invoke_dbo_tool` with arguments that match the known input schema exactly."
)

@mcp.tool(
    description=(
        "Read-only OCI compartment resolution. Use this when the compartment OCID is already known and you want to "
        "validate or inspect that exact compartment before invoking compartment-scoped Database Observability tools. "
        "Do not invent an OCID. If the user provides a compartment name rather than an OCID, use "
        "`resolve_oci_compartment` first."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def get_oci_compartment(
    compartment_id: str = Field(
        ...,
        description="Required OCI compartment OCID to validate or inspect, for example ocid1.compartment...",
    ),
) -> Any:
    if not compartment_id:
        raise ValueError("compartment_id is required.")
    client = identity_bootstrap_client()
    return serialize_response(client.get_compartment(compartment_id=compartment_id))


@mcp.tool(
    description=(
        "Read-only OCI compartment name-to-OCID resolver. Use this when the user supplies a compartment name instead "
        "of an OCID. It searches all configured DBO_MCP_TENANCY_IDS, or the optional explicit "
        "root_compartment_ids. It returns matching compartment OCIDs and display-name paths. Use a unique match's "
        "id as compartment_id in subsequent operations. If the result is ambiguous, ask the user to select a path; "
        "do not choose a match automatically."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def resolve_oci_compartment(
    name: str = Field(..., description="Required exact compartment display name to resolve, matched case-insensitively."),
    root_compartment_ids: Annotated[
        list[str] | None,
        Field(description="Optional compartment or tenancy OCID roots. When omitted, searches DBO_MCP_TENANCY_IDS."),
    ] = None,
) -> dict[str, Any]:
    if not name or not name.strip():
        raise ValueError("name is required.")
    roots = _discovery_roots(root_compartment_ids)
    client = identity_bootstrap_client()
    matches: list[dict[str, str]] = []
    target_name = name.strip().casefold()
    for root_id in roots:
        compartments = _compartments_under_root(client, root_id)
        for compartment_id, compartment in compartments.items():
            if str(compartment.get("name", "")).casefold() == target_name:
                matches.append(
                    {
                        "id": compartment_id,
                        "name": str(compartment["name"]),
                        "path": _compartment_path(compartment_id, compartments, root_id),
                        "rootCompartmentId": root_id,
                    }
                )
    matches.sort(key=lambda match: (match["path"].casefold(), match["id"]))
    return {
        "name": name.strip(),
        "resolution": "unique" if len(matches) == 1 else "ambiguous" if matches else "not_found",
        "matches": matches,
    }

@mcp.tool(
    description=(
        "Read-only OCI compartment name-to-OCID resolution. Use this when the user supplies a compartment name instead "
        "of an OCID. Set `name` to the requested name and use the returned item's `id` as "
        "the `compartment_id` in subsequent Database Observability operations. The root may be supplied explicitly, "
        "or omitted when exactly one DBO_MCP_TENANCY_IDS value is configured. For general name resolution "
        "across configured roots, prefer `resolve_oci_compartment`."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def list_oci_compartments(
    root_compartment_id: Annotated[
        str | None,
        Field(description="Optional root compartment OCID from which to search. When omitted, uses the sole DBO_MCP_TENANCY_IDS value."),
    ] = None,
    include_subtree: bool = Field(True, description="When true, include all descendant compartments below the root."),
    access_level: str = Field("ACCESSIBLE", description="Visibility filter: ACCESSIBLE returns compartments available to the caller; ANY includes all visible states."),
    name: str | None = Field(None, description="Optional exact compartment-name filter for resolving a user-provided name to one or more compartment OCIDs."),
    lifecycle_state: str | None = Field(None, description="Optional lifecycle-state filter, such as ACTIVE."),
    limit: int = Field(50, ge=1, le=1000, description="Maximum number of compartments returned in this response."),
    page: str | None = Field(None, description="Optional nextPage token returned by a previous call."),
) -> dict[str, Any]:
    if not root_compartment_id:
        roots = _configured_compartment_roots()
        if len(roots) != 1:
            raise ValueError(
                "root_compartment_id is required unless exactly one DBO_MCP_TENANCY_IDS value is configured."
            )
        root_compartment_id = roots[0]
    client = identity_bootstrap_client()
    response = client.list_compartments(
        compartment_id=root_compartment_id,
        compartment_id_in_subtree=include_subtree,
        access_level=access_level,
        name=name,
        lifecycle_state=lifecycle_state,
        limit=limit,
        page=page,
    )
    result = serialize_response(response)
    items = result["data"]
    return {"items": items, "count": len(items), "nextPage": result["nextPage"]}

@mcp.tool(
    description="Entry point for Oracle Database Observability capability discovery. Returns a compact list of available "
        "skills with short summaries only; does not return operation schemas. Use it when the relevant capability "
                "is not already known, and reuse prior results when they remain applicable."
)
def list_dbo_skills() -> dict[str, Any]:
    return {"skills": [{"name": s["name"], "description": s["description"], "toolCount": len(s["tools"])} for s in registry.skills]}

@mcp.tool(
    description="Discovery endpoint for Oracle Database Observability operations within selected skills. Use it to list "
                "candidate operations when the required operation is not already known. Reuse prior results when they "
                "remain applicable, and use `describe_dbo_tool` to inspect or confirm a schema when needed before "
                "calling `invoke_dbo_tool`."
)
def list_dbo_tools(
    skill_names: list[str] = Field(
        ...,
        min_length=1,
        description="Required skill names returned by list_dbo_skills. Only tools belonging to these skills are listed.",
    ),
    limit: int = Field(50, ge=1, le=100, description="Maximum compact tool entries to return."),
) -> dict[str, Any]:
    selected = set(skill_names)
    tools = registry.list_tools(selected)
    return {
        "count": len(tools),
        "tools": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "skills": list(tool["skills"]),
                "mutable": tool["mutable"],
                "compartmentRequirements": compartment_requirements(tool),
            }
            for tool in tools[:limit]
        ],
        "truncated": len(tools) > limit,
    }

@mcp.tool(
    description="Retrieve the complete invocation contract for one Oracle Database Observability tool selected from "
                "`list_dbo_tools`. Returns the exact JSON input schema, required fields, and mutability metadata. "
                "Call it when the schema is unavailable, outdated, or uncertain; a previously retrieved applicable "
                "schema may be reused."
)
def describe_dbo_tool(
    tool_name: str = Field(..., description="Required logical tool name returned by list_dbo_tools, for example list_database_insights."),
) -> dict[str, Any]:
    tool = registry.get_tool(tool_name)
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": to_jsonable(tool["inputSchema"]),
        "mutable": tool["mutable"],
        "compartmentRequirements": compartment_requirements(tool),
        "guidance": "Pass an arguments object that exactly matches inputSchema.",
    }

@mcp.tool(
    description="Invoke one registered Oracle Database Observability operation. The supplied arguments object must "
                "conform exactly to the selected tool's known `inputSchema`. For an operation with compartment "
                "requirements, provide a real compartment OCID or resolve a user-provided name from a user-provided "
                "root compartment OCID; do not invent an OCID. Use `describe_dbo_tool` first when that schema is "
                "unavailable, outdated, or uncertain. This is the sole endpoint that executes catalog "
                "operations."
)
def invoke_dbo_tool(
    tool_name: str = Field(..., description="Required logical tool name returned by list_dbo_tools or otherwise known."),
    arguments: dict[str, Any] = Field(default_factory=dict, description="Required JSON object of SDK arguments matching the exact known inputSchema."),
) -> Any:
    return invoke_registered_tool(registry.get_tool(tool_name), arguments)
