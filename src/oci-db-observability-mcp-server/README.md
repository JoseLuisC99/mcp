# OCI Database Observability MCP Server

This package provides one MCP server for OCI Operations Insights (OPSI),
Database Management (DBM), and OCI Monitoring observability workflows. It
exposes three read-only compartment discovery tools plus four catalog discovery
and invocation tools. The catalog contains read-only OCI SDK bindings and
packaged metadata handlers.

## Running

### STDIO

```sh
uvx oracle.oci-db-observability-mcp-server
```

Use a named OCI CLI profile when required:

```sh
OCI_CONFIG_PROFILE=<profile_name> uvx oracle.oci-db-observability-mcp-server
```

### HTTP streaming

```sh
ORACLE_MCP_HOST=<bind_host> \
ORACLE_MCP_PORT=<port> \
ORACLE_MCP_BASE_URL=<public_base_url> \
OCI_REGION=<region> \
IDCS_DOMAIN=<idcs_domain> \
IDCS_CLIENT_ID=<client_id> \
IDCS_CLIENT_SECRET=<client_secret> \
IDCS_AUDIENCE=<audience> \
uvx oracle.oci-db-observability-mcp-server
```

Register `${ORACLE_MCP_BASE_URL}/auth/callback` in the OCI IAM confidential
application. If `IDCS_REQUIRED_SCOPES` is unset, the default scope is
`oci_mcp.db_observability.invoke` together with the standard OpenID scopes.
The server uses `oracle-mcp-common` 0.1.2 or later for OCI authentication.

## Discovery workflow

1. For a compartment-scoped operation, use a user-provided compartment OCID.
   For a name lookup, call `resolve_oci_compartment`; it searches the configured
   discovery roots and returns matching OCIDs and paths. Configure one or more
   comma-separated roots independently of OCI authentication:

   ```sh
   DBO_MCP_TENANCY_IDS=<tenancy_ocid>[,<another_tenancy_ocid>]
   ```

   Use the `id` from a unique match as `compartment_id` in subsequent operations.
   If there are multiple matches, ask the user to select a returned path. If no
   roots are configured, provide `root_compartment_ids` to the resolver or ask
   the user for a root; do not invent an OCID. `list_oci_compartments` supports
   direct browsing below an explicit root, or uses the configured root when
   exactly one is configured. If the user provides an OCID, call
   `get_oci_compartment` to validate it when needed.
2. Call `list_dbo_skills` when the relevant capability is not already known.
3. Call `list_dbo_tools` for selected skills when the required operation is not
   already known.
4. Call `describe_dbo_tool` when the selected tool's schema is unavailable,
   outdated, or uncertain. Previously retrieved applicable results may be
   reused.
5. Call `invoke_dbo_tool` with arguments matching the known schema.

The complete skill and tool catalogs are packaged as JSON under
`oracle/oci_db_observability_mcp_server/metadata`. Skills organize
read-only workflows such as inventory, diagnostics, performance, and fleet
analysis. Their concise descriptions are MCP discovery metadata, not MCP
resources; the detailed OEM.ai skill files are not packaged or required at
runtime. The tool catalog contains OPSI, DBM, and OCI Monitoring operations;
it does not expose the individual operations as MCP tools.

## OCI Monitoring metric and alarm workflow

The `database-and-infra-observability-metric-catalog` skill provides three
local catalog tools (search, get, and list), one live metric reader, and three
read-only alarm tools. First identify a metric using the local catalog, then
read a specific metric with its exact namespace/name, explicit RFC 3339 time
window, allowed dimensions, aggregation, interval, and resolution. The metric
reader turns that request into MQL and makes one
`MonitoringClient.summarize_metrics_data` call. Alarm definition and state
tools each make one respective OCI Monitoring SDK call. No tool changes OCI
resources.

## Pagination

Paginated operations accept an optional `page` argument. Responses contain the
page data and a `nextPage` token when more results are available:

```json
{
  "data": [],
  "nextPage": "<token>"
}
```

Pass the returned `nextPage` value as `page` in the next invocation. Stop when
`nextPage` is null.

## Development

Run the unified server tests with:

```sh
make test project=oci-db-observability-mcp-server
```

Run repository lint with:

```sh
make lint
```

## Third-Party APIs

Developers choosing to distribute a binary implementation of this project are
responsible for obtaining and providing all required licenses and copyright
notices for the third-party code used.

## License

Copyright (c) 2026 Oracle and/or its affiliates.

Released under the Universal Permissive License v1.0.
