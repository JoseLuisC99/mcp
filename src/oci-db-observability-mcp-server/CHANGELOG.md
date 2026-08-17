# Changelog

## 0.1.0 - 2026-08-05

### Server

- Unified, read-only Oracle Database Observability MCP server for OCI
  Operations Insights (OPSI) and Database Management (DBM).

### Transports

- STDIO and HTTP streaming transports.

### Authentication

- Configured OCI authentication through `oracle-mcp-common`, including
  API-key, security-token, instance-principal, and resource-principal flows.
- OCI IAM/IDCS request-token authentication for HTTP deployments.

### Tool surface

- Compartment scope discovery through `get_oci_compartment` and
  `list_oci_compartments`.
- Catalog discovery and dispatch through `list_dbo_skills`, `list_dbo_tools`,
  `describe_dbo_tool`, and `invoke_dbo_tool`.
- 34 workflow skills and 229 read-only OPSI/DBM SDK-backed catalog operations.

### Package and entry point

- Distribution: `oracle.oci-db-observability-mcp-server`.
- Console entry point: `oracle.oci-db-observability-mcp-server`.
- Python package: `oracle.oci_db_observability_mcp_server`.
- Discovery guidance permits reuse of applicable skill, tool, and schema
  information from earlier calls instead of requiring the full workflow for
  every invocation.
