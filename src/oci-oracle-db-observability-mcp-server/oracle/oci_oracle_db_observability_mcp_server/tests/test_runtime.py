from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oracle.oci_oracle_db_observability_mcp_server import __project__, __version__
from oracle.oci_oracle_db_observability_mcp_server import runtime


EXPECTED_USER_AGENT = f"{__project__.split('oracle.', 1)[1].removesuffix('-server')}/{__version__}"


class FakeClient:
    def __init__(self, config, signer=None):
        self.config = config
        self.signer = signer


@pytest.mark.parametrize("auth_type", ["api_key", "security_token", "instance_principal"])
def test_stdio_client_uses_common_auth_context_and_user_agent(monkeypatch, auth_type) -> None:
    runtime._stdio_client.cache_clear()
    signer = object()
    context = SimpleNamespace(
        auth_type=auth_type,
        config={"region": "us-phoenix-1"},
        signer=signer,
        tenancy_id="tenant",
    )
    build_context = Mock(return_value=context)
    monkeypatch.setattr(runtime, "build_auth_context", build_context)
    monkeypatch.setattr(runtime, "get_access_token", lambda: None)
    monkeypatch.setattr(runtime, "client_class", lambda _service, _client: FakeClient)

    client = runtime._client("opsi", "OperationsInsightsClient")

    build_context.assert_called_once_with()
    assert client.signer is signer
    assert client.config == {"region": "us-phoenix-1", "additional_user_agent": EXPECTED_USER_AGENT}
    assert context.auth_type == auth_type


def test_http_clients_are_caller_specific_and_not_cached(monkeypatch) -> None:
    runtime._stdio_client.cache_clear()
    first_signer, second_signer = object(), object()
    contexts = {
        "first-token": SimpleNamespace(config={"region": "us-chicago-1"}, signer=first_signer),
        "second-token": SimpleNamespace(config={"region": "us-chicago-1"}, signer=second_signer),
    }
    calls: list[str] = []

    def context_for(token: str):
        calls.append(token)
        return contexts[token]

    tokens = iter([SimpleNamespace(token="first-token"), SimpleNamespace(token="second-token")])
    monkeypatch.setattr(runtime, "_http_auth", SimpleNamespace(context_for=context_for))
    monkeypatch.setattr(runtime, "get_access_token", lambda: next(tokens))
    monkeypatch.setattr(runtime, "build_auth_context", lambda: pytest.fail("HTTP must not use stdio credentials"))
    monkeypatch.setattr(runtime, "client_class", lambda _service, _client: FakeClient)

    first = runtime._client("opsi", "OperationsInsightsClient")
    second = runtime._client("opsi", "OperationsInsightsClient")

    assert calls == ["first-token", "second-token"]
    assert first is not second
    assert first.signer is first_signer
    assert second.signer is second_signer
    assert first.config["additional_user_agent"] == EXPECTED_USER_AGENT
    assert second.config["additional_user_agent"] == EXPECTED_USER_AGENT


def test_http_access_token_requires_initialized_policy(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_http_auth", None)

    with pytest.raises(RuntimeError, match="policy has not been initialized"):
        runtime._get_config_and_signer(SimpleNamespace(token="caller-token"))


def test_identity_bootstrap_uses_http_context_and_tenancy_override(monkeypatch) -> None:
    signer = object()
    monkeypatch.setattr(
        runtime,
        "_http_auth",
        SimpleNamespace(context_for=lambda token: SimpleNamespace(config={"region": "us-ashburn-1"}, signer=signer)),
    )
    monkeypatch.setattr(runtime, "get_access_token", lambda: SimpleNamespace(token="caller-token"))
    monkeypatch.setenv("OCI_MCP_TENANCY_ID_OVERRIDE", "ocid1.tenancy.oc1..example")
    monkeypatch.setattr(runtime, "client_class", lambda _service, _client: FakeClient)

    client, tenancy_id = runtime.identity_bootstrap_client()

    assert isinstance(client, FakeClient)
    assert client.signer is signer
    assert tenancy_id == "ocid1.tenancy.oc1..example"


def test_identity_bootstrap_requires_tenancy(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "get_access_token", lambda: None)
    monkeypatch.setattr(
        runtime,
        "build_auth_context",
        lambda: SimpleNamespace(config={}, signer=object(), tenancy_id=None),
    )
    monkeypatch.delenv("OCI_MCP_TENANCY_ID_OVERRIDE", raising=False)

    with pytest.raises(ValueError, match="missing tenancy"):
        runtime.identity_bootstrap_client()


def test_invoke_validates_before_creating_sdk_client(monkeypatch) -> None:
    tool = {
        "name": "example_tool",
        "service": "opsi",
        "client": "OperationsInsightsClient",
        "operation": "get_example",
        "inputSchema": {
            "type": "object",
            "properties": {"compartment_id": {"type": "string"}},
            "required": ["compartment_id"],
            "additionalProperties": False,
        },
    }
    monkeypatch.setattr(runtime, "_client", lambda *_args: pytest.fail("client must not be created"))

    with pytest.raises(ValueError, match="compartment_id"):
        runtime.invoke_registered_tool(tool, {})


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("list_database_insights", {"bogus": 1}),
        ("list_database_insights", {"database_id": ["ocid1.database.oc1..example", 7]}),
        (
            "create_managed_database_group",
            {
                "create_managed_database_group_details": {
                    "name": "group",
                    "compartment_id": "ocid1.compartment.oc1..example",
                    "bogus": "value",
                }
            },
        ),
    ],
)
def test_registered_tool_rejects_invalid_sdk_schema_arguments_before_client_creation(monkeypatch, tool_name, arguments) -> None:
    from oracle.oci_oracle_db_observability_mcp_server.registry import load_registry

    monkeypatch.setattr(runtime, "_client", lambda *_args: pytest.fail("client must not be created"))

    with pytest.raises(ValueError, match="Invalid arguments"):
        runtime.invoke_registered_tool(load_registry().get_tool(tool_name), arguments)


def test_response_serialization_and_model_helpers(monkeypatch) -> None:
    assert runtime.serialize_response(SimpleNamespace(data={"value": "ok"}, headers={"opc-next-page": "page-2"})) == {
        "data": {"value": "ok"},
        "nextPage": "page-2",
    }
    monkeypatch.setattr(runtime.oci.util, "to_dict", lambda _data: (_ for _ in ()).throw(TypeError("bad data")))
    assert runtime.serialize_response(SimpleNamespace(data="fallback", headers={})) == {
        "data": "fallback",
        "nextPage": None,
    }
    assert runtime._type_name("ExampleDetails") == "ExampleDetails"
    assert runtime._type_name(int) == "int"
    assert runtime._type_name(object()) is None

    class Details:
        swagger_types = {"child": "ChildDetails"}

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ChildDetails:
        swagger_types = {}

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    models = SimpleNamespace(Details=Details, ChildDetails=ChildDetails)
    monkeypatch.setattr(runtime.oci.util, "from_dict", lambda model, payload: (model, payload), raising=False)
    assert runtime._coerce({"child": {"name": "child"}}, "Details", models) == (
        Details,
        {"child": (ChildDetails, {"name": "child"})},
    )
    monkeypatch.setattr(runtime.oci.util, "from_dict", lambda *_args: (_ for _ in ()).throw(ValueError("use constructor")))
    assert runtime._coerce({"name": "details"}, "Details", models).kwargs == {"name": "details"}
    assert runtime._coerce({"name": "unchanged"}, "Missing", models) == {"name": "unchanged"}


def test_coerce_arguments_uses_sdk_doc_types_for_generated_kwargs(monkeypatch) -> None:
    class Details:
        swagger_types = {"name": "str"}

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Client:
        def create_example(self, **kwargs):
            """:param ExampleDetails details: (required) Request details."""
            return kwargs

    monkeypatch.setattr(runtime, "_models_module", lambda _client: SimpleNamespace(ExampleDetails=Details))
    result = runtime._coerce_arguments(Client(), "create_example", {"details": {"name": "example"}})

    assert isinstance(result["details"], Details)
    assert result["details"].kwargs == {"name": "example"}


def test_registered_tool_coerces_arguments_serializes_and_wraps_oci_errors(monkeypatch) -> None:
    tool = {
        "name": "example_tool",
        "service": "opsi",
        "client": "OperationsInsightsClient",
        "operation": "get_example",
        "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}, "additionalProperties": False},
    }

    class Client:
        def get_example(self, value: str):
            return SimpleNamespace(data={"value": value}, headers={})

    monkeypatch.setattr(runtime, "_client", lambda *_args: Client())
    assert runtime.invoke_registered_tool(tool, {"value": "ok"}) == {
        "data": {"value": "ok"},
        "nextPage": None,
    }
    with pytest.raises(ValueError, match="arguments must be a JSON object"):
        runtime.invoke_registered_tool(tool, [])

    class FailingClient:
        def get_example(self, value: str):
            raise RuntimeError("SDK failure")

    monkeypatch.setattr(runtime, "_client", lambda *_args: FailingClient())
    with pytest.raises(RuntimeError, match="OCI operation example_tool failed: SDK failure"):
        runtime.invoke_registered_tool(tool, {"value": "ok"})
