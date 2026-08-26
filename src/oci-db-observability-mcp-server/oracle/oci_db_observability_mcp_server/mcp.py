"""
Copyright (c) 2026, Oracle and/or its affiliates.
Licensed under the Universal Permissive License v1.0 as shown at
https://oss.oracle.com/licenses/upl.
"""

from __future__ import annotations
from typing import Any
from fastmcp import FastMCP
from pydantic import Field
from .registry import compartment_requirements, load_registry, to_jsonable
from .runtime import invoke_registered_tool
from .runtime import identity_bootstrap_client, serialize_response

registry = load_registry()
mcp = FastMCP(
    name="oracle.oci-db-observability-mcp-server",
    instructions="For a compartment-scoped operation, use a user-provided compartment OCID, or resolve a user-provided compartment name with "
                 "`list_oci_compartments` using an explicit root compartment OCID. If neither is available, ask the user for a compartment OCID or "
                 "root compartment OCID; do not invent an OCID. Validate an already known OCID with `get_oci_compartment` when scope is not already known. Pass the resolved compartment OCID "
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
        "Do not invent an OCID. If the user provides a compartment name rather than an OCID, request a root "
        "compartment OCID and use `list_oci_compartments` first."
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
        "Read-only OCI compartment name-to-OCID resolution. Use this when the user supplies a compartment name instead "
        "of an OCID. Set `name` to the requested name and use the returned item's `id` as "
        "the `compartment_id` in subsequent Database Observability operations. Provide a user-supplied "
        "`root_compartment_id` as the compartment OCID from which to search descendants; if it is unavailable, ask "
        "the user rather than inventing one. If multiple compartments share a name, "
        "inspect their IDs and hierarchy before continuing."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def list_oci_compartments(
    root_compartment_id: str = Field(..., description="Required root compartment OCID from which to search for matching compartments and descendants."),
    include_subtree: bool = Field(True, description="When true, include all descendant compartments below the root."),
    access_level: str = Field("ACCESSIBLE", description="Visibility filter: ACCESSIBLE returns compartments available to the caller; ANY includes all visible states."),
    name: str | None = Field(None, description="Optional exact compartment-name filter for resolving a user-provided name to one or more compartment OCIDs."),
    lifecycle_state: str | None = Field(None, description="Optional lifecycle-state filter, such as ACTIVE."),
    limit: int = Field(50, ge=1, le=1000, description="Maximum number of compartments returned in this response."),
    page: str | None = Field(None, description="Optional nextPage token returned by a previous call."),
) -> dict[str, Any]:
    if not root_compartment_id:
        raise ValueError("root_compartment_id is required.")
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
