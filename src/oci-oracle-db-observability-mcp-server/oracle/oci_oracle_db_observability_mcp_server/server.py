"""Unified DBO MCP process entry point."""
from __future__ import annotations

import os

from fastmcp.utilities.auth import parse_scopes
from oracle_mcp_common import build_idcs_http_auth

from .mcp import mcp
from .runtime import configure_http_auth


def main() -> None:
    host, port = os.getenv("ORACLE_MCP_HOST"), os.getenv("ORACLE_MCP_PORT")
    if not (host and port):
        mcp.run()
        return
    policy = build_idcs_http_auth(parse_scopes(os.getenv("IDCS_REQUIRED_SCOPES")) or ["openid", "profile", "email", "oci_mcp.db_observability.invoke"])
    mcp.auth = policy.provider
    configure_http_auth(policy)
    mcp.run(transport="http", host=host, port=int(port))


if __name__ == "__main__":
    main()
