from __future__ import annotations
from typing import Any
from fastmcp import FastMCP
from pydantic import Field
from .registry import load_registry, to_jsonable
from .runtime import invoke_registered_tool
from .runtime import identity_bootstrap_client, serialize_response

registry = load_registry()
mcp = FastMCP(
    name="oracle.oci-db-observability-mcp-server",
    instructions="Resolve OCI scope with `list_compartments` or `get_compartment`. Use `list_dbo_skills` to discover "
                 "the smallest relevant Oracle Database Observability capability for the user's goal, then `list_dbo_tools` "
                 "to list candidate operations. Before execution, call `describe_dbo_tool` to get the exact contract, then "
                 "`invoke_dbo_tool` with arguments that match the returned input schema exactly."
)

@mcp.tool(
    description=(
        "Read-only OCI scope discovery. Retrieve one compartment by its OCID to validate "
        "the target compartment before discovering Database Observability tools or resources."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def get_oci_compartment(
    compartment_id: str = Field(
        ...,
        description="Required OCI compartment OCID to retrieve, for example ocid1.compartment...",
    ),
) -> Any:
    if not compartment_id:
        raise ValueError("compartment_id is required.")
    client, _ = identity_bootstrap_client()
    return serialize_response(client.get_compartment(compartment_id=compartment_id))

@mcp.tool(
    description=(
        "Read-only OCI scope discovery. List accessible compartments and their OCIDs before "
        "selecting a Database Observability skill or querying compartment-scoped resources."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def list_oci_compartments(
    root_compartment_id: str | None = Field(None, description="Optional root compartment OCID. Omit it to start at the authenticated tenancy."),
    include_subtree: bool = Field(True, description="When true, include all descendant compartments below the root."),
    access_level: str = Field("ACCESSIBLE", description="Visibility filter: ACCESSIBLE returns compartments available to the caller; ANY includes all visible states."),
    name: str | None = Field(None, description="Optional exact compartment-name filter."),
    lifecycle_state: str | None = Field(None, description="Optional lifecycle-state filter, such as ACTIVE."),
    limit: int = Field(50, ge=1, le=1000, description="Maximum number of compartments returned in this response."),
    page: str | None = Field(None, description="Optional nextPage token returned by a previous call."),
) -> dict[str, Any]:
    client, tenancy_id = identity_bootstrap_client()
    response = client.list_compartments(
        compartment_id=root_compartment_id or tenancy_id,
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
                "skills with short summaries only; does not return operation schemas. Use this to resolve the smallest "
                "relevant capability for the user’s request, then call `list_dbo_tools` for executable operations."
)
def list_dbo_skills() -> dict[str, Any]:
    return {"skills": [{"name": s["name"], "description": s["description"], "toolCount": len(s["tools"])} for s in registry.skills]}

@mcp.tool(
    description="Discovery endpoint for Oracle Database Observability operations within selected skills. Use it to list "
                "candidate operations, then resolve a candidate with `describe_dbo_tool` before calling "
                "`invoke_dbo_tool`."
)
def list_dbo_tools(
    skill_names: list[str] = Field(
        ...,
        min_length=1,
        description="Required skill names returned by list_skills. Only tools belonging to these skills are searched.",
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
            }
            for tool in tools[:limit]
        ],
        "truncated": len(tools) > limit,
    }

@mcp.tool(
    description="Retrieve the complete invocation contract for one Oracle Database Observability tool selected from "
                "`list_dbo_tools`. Returns the exact JSON input schema, required fields, and mutability metadata. "
                "Must be called immediately before `invoke_dbo_tool`."
)
def describe_dbo_tool(
    tool_name: str = Field(..., description="Required logical tool name returned by list_tools, for example list_database_insights."),
) -> dict[str, Any]:
    tool = registry.get_tool(tool_name)
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": to_jsonable(tool["inputSchema"]),
        "mutable": tool["mutable"],
        "guidance": "Pass an arguments object that exactly matches inputSchema.",
    }

@mcp.tool(
    description="Invoke one registered Oracle Database Observability operation. Must be preceded by `describe_dbo_tool`, "
                "and the supplied arguments object must conform exactly to the selected tool's `inputSchema`. This is "
                "the sole endpoint that executes catalog operations."
)
def invoke_dbo_tool(
    tool_name: str = Field(..., description="Required logical tool name returned by list_tools and described with describe_tool."),
    arguments: dict[str, Any] = Field(default_factory=dict, description="Required JSON object of SDK arguments matching the exact inputSchema from describe_tool."),
) -> Any:
    return invoke_registered_tool(registry.get_tool(tool_name), arguments)
