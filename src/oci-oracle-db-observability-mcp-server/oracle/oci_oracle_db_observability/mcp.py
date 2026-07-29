"""The four-tool public MCP surface."""
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
    instructions=(
        "Use list_compartments or get_compartment to resolve OCI scope. Then call "
        "list_skills, select the smallest relevant skills, and call list_tools. Use "
        "separate keywords such as ['database', 'insights'], not one underscored SDK "
        "name. Call describe_tool before invoke_tool."
    ),
)

@mcp.tool(
    description=(
        "Read-only OCI scope discovery. Retrieve one compartment by its OCID to validate "
        "the target compartment before discovering Database Observability tools or resources."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
def get_compartment(
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
def list_compartments(
    root_compartment_id: str | None = Field(None, description="Optional root compartment OCID. Omit it to start at the authenticated tenancy."),
    include_subtree: bool = Field(True, description="When true, include all descendant compartments below the root."),
    access_level: str = Field("ACCESSIBLE", description="Visibility filter: ACCESSIBLE returns compartments available to the caller; ANY includes all visible states."),
    name: str | None = Field(None, description="Optional exact compartment-name filter."),
    lifecycle_state: str | None = Field(None, description="Optional lifecycle-state filter, such as ACTIVE."),
    limit: int = Field(50, ge=1, le=1000, description="Maximum number of compartments returned in this response."),
) -> dict[str, Any]:
    client, tenancy_id = identity_bootstrap_client()
    response = client.list_compartments(
        compartment_id=root_compartment_id or tenancy_id,
        compartment_id_in_subtree=include_subtree,
        access_level=access_level,
        name=name,
        lifecycle_state=lifecycle_state,
        limit=limit,
    )
    items = serialize_response(response)
    return {"items": items, "count": len(items)}

@mcp.tool(
    description=(
        "Start Database Observability discovery here. Returns compact skill summaries, not "
        "operation schemas. Select the smallest skills relevant to the user's goal, then call list_tools."
    )
)
def list_skills() -> dict[str, Any]:
    return {"skills": [{"name": s["name"], "description": s["description"], "toolCount": len(s["tools"])} for s in registry.skills]}

@mcp.tool(
    description=(
        "Discover registered OCI operations within selected skills. Use keywords as separate "
        "plain-language terms that must all match a tool name or description; for example, "
        "use ['database', 'insights'] to find list_database_insights. Do not use an underscored "
        "SDK operation name as one keyword. Call describe_tool on a selected result before invoke_tool."
    )
)
def list_tools(
    skill_names: list[str] = Field(
        ...,
        min_length=1,
        description="Required skill names returned by list_skills. Only tools belonging to these skills are searched.",
    ),
    keywords: list[str] | None = Field(
        None,
        description="Optional separate search terms. Every term must match the tool name or description, case-insensitively; use ['database', 'insights'], not ['database_insights'].",
    ),
    limit: int = Field(50, ge=1, le=100, description="Maximum compact tool entries to return; use a narrow keyword search before increasing this."),
) -> dict[str, Any]:
    selected = set(skill_names)
    tools = registry.list_tools(selected, keywords)
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
    description=(
        "Get the complete invocation contract for one tool returned by list_tools. Returns the "
        "exact JSON input schema, required arguments, and mutability. Use immediately before invoke_tool."
    )
)
def describe_tool(
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
    description=(
        "Execute one registered OCI Database Observability operation. First call describe_tool, "
        "then supply an arguments object that conforms exactly to its inputSchema. This is the only "
        "tool that invokes a catalog operation."
    )
)
def invoke_tool(
    tool_name: str = Field(..., description="Required logical tool name returned by list_tools and described with describe_tool."),
    arguments: dict[str, Any] = Field(default_factory=dict, description="Required JSON object of SDK arguments matching the exact inputSchema from describe_tool."),
) -> Any:
    return invoke_registered_tool(registry.get_tool(tool_name), arguments)
