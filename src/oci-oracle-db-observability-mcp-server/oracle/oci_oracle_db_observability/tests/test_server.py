from __future__ import annotations

from types import SimpleNamespace

from oracle.oci_oracle_db_observability import server


def test_main_runs_stdio_without_http_environment(monkeypatch) -> None:
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.delenv("ORACLE_MCP_HOST", raising=False)
    monkeypatch.delenv("ORACLE_MCP_PORT", raising=False)
    monkeypatch.setattr(server.mcp, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    server.main()

    assert calls == [((), {})]


def test_main_configures_idcs_http_auth(monkeypatch) -> None:
    calls: list[tuple[tuple, dict]] = []
    policy = SimpleNamespace(provider=object())
    monkeypatch.setenv("ORACLE_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("ORACLE_MCP_PORT", "8080")
    monkeypatch.setenv("IDCS_REQUIRED_SCOPES", "openid custom.scope")
    monkeypatch.setattr(server, "build_idcs_http_auth", lambda scopes: policy)
    monkeypatch.setattr(server, "configure_http_auth", lambda actual: calls.append((("policy", actual), {})))
    monkeypatch.setattr(server.mcp, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    server.main()

    assert server.mcp.auth is policy.provider
    assert calls == [(("policy", policy), {}), ((), {"transport": "http", "host": "127.0.0.1", "port": 8080})]
