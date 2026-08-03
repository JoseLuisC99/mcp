# OCI Database Observability MCP Server

This package provides one MCP server for OCI Operations Insights (OPSI) and OCI
Database Management (DBM). It exposes two read-only compartment discovery
tools plus four catalog discovery and invocation tools. The catalog contains
only operations with exact OCI Python SDK bindings.

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
The server uses `oracle-mcp-common` 0.1.2 or later for OCI authentication. For
HTTP token-exchange authentication, set `OCI_MCP_TENANCY_ID_OVERRIDE` when the
caller signer does not expose a tenancy and `list_compartments` is called
without `root_compartment_id`.

## Discovery workflow

1. Call `list_oci_compartments` or `get_oci_compartment` to resolve OCI scope.
2. Call `list_dbo_skills` and select the smallest relevant skill set.
3. Call `list_dbo_tools` for those skills, optionally with separate `keywords`.
   Every keyword must match a tool name or description; use
   `['database', 'insights']`, not `['database_insights']`.
4. Call `describe_dbo_tool` for the chosen operation.
5. Call `invoke_dbo_tool` with arguments matching the returned schema.

The complete skill and tool catalogs are packaged as JSON under
`oracle/oci_oracle_db_observability/metadata`. The tool catalog contains
OPSI and DBM SDK bindings; it does not expose the individual operations as MCP
tools.

## Development

Run the unified server tests with:

```sh
make test project=oci-oracle-db-observability-mcp-server
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
