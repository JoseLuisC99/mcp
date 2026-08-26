"""
Copyright (c) 2026, Oracle and/or its affiliates.
Licensed under the Universal Permissive License v1.0 as shown at
https://oss.oracle.com/licenses/upl.

Single allowlisted OCI Python SDK dispatcher.
"""
from __future__ import annotations

import inspect
import json
from functools import lru_cache
from typing import Any, Mapping, get_args, get_origin

import oci
from jsonschema import Draft202012Validator
from fastmcp.server.dependencies import get_access_token
from oracle_mcp_common import IDCSHttpAuth, build_auth_context

from . import __project__, __version__
from .registry import client_class, compartment_requirements
from .sdk_schema import parameter_docs

_USER_AGENT = f"{__project__.split('oracle.', 1)[1].removesuffix('-server')}/{__version__}"
_http_auth: IDCSHttpAuth | None = None


def configure_http_auth(policy: IDCSHttpAuth) -> None:
    """Configure caller-scoped OCI authentication for HTTP transport."""
    global _http_auth
    _http_auth = policy


def _get_http_config_and_signer(access_token: Any) -> tuple[dict[str, Any], Any]:
    """Build caller-specific OCI SDK authentication for one HTTP request."""
    if _http_auth is None:
        raise RuntimeError("HTTP authentication policy has not been initialized.")
    context = _http_auth.context_for(access_token.token if access_token else None)
    return ({**context.config, "additional_user_agent": _USER_AGENT}, context.signer)


def _get_config_and_signer(access_token: Any | None = None) -> tuple[dict[str, Any], Any]:
    """Resolve HTTP caller credentials or configured stdio OCI credentials."""
    if access_token is not None:
        return _get_http_config_and_signer(access_token)
    context = build_auth_context()
    return ({**context.config, "additional_user_agent": _USER_AGENT}, context.signer)


@lru_cache(maxsize=None)
def _stdio_client(service: str, client_name: str) -> Any:
    config, signer = _get_config_and_signer()
    return client_class(service, client_name)(config, signer=signer)


def _client(service: str, client_name: str) -> Any:
    access_token = get_access_token()
    if access_token is None:
        return _stdio_client(service, client_name)
    config, signer = _get_config_and_signer(access_token)
    return client_class(service, client_name)(config, signer=signer)


def identity_bootstrap_client() -> Any:
    """Create the Identity client for explicit compartment lookups."""
    config, signer = _get_config_and_signer(get_access_token())
    client = client_class("identity", "IdentityClient")(config, signer=signer)
    return client


def _serialize_data(data: Any) -> Any:
    """Convert a response payload to a JSON-safe value."""
    try:
        return json.loads(json.dumps(oci.util.to_dict(data), default=str))
    except Exception:
        return str(data)


def serialize_response(response: Any) -> dict[str, Any]:
    """Convert an OCI SDK response to JSON-safe data and pagination metadata."""
    data = getattr(response, "data", response)
    headers = getattr(response, "headers", {}) or {}
    return {"data": _serialize_data(data), "nextPage": headers.get("opc-next-page")}


def _models_module(client: type[Any]) -> Any:
    module = client.__module__.rsplit(".", 1)[0]
    try:
        return __import__(f"{module}.models", fromlist=["models"])
    except ImportError:
        return None


def _type_name(annotation: Any) -> str | None:
    if isinstance(annotation, str):
        return annotation
    if annotation is inspect.Signature.empty:
        return None
    if isinstance(annotation, type):
        return annotation.__name__
    origin = get_origin(annotation)
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    return _type_name(args[0]) if origin and len(args) == 1 else None


def _coerce(value: Any, type_name: str | None, models: Any) -> Any:
    if value is None or not type_name or not isinstance(value, dict) or models is None:
        return value
    model = getattr(models, type_name.rsplit(".", 1)[-1], None)
    if not inspect.isclass(model):
        return value
    model = getattr(models, value.get("model_type", ""), model)
    payload = {key: item for key, item in value.items() if key != "model_type"}
    swagger = getattr(model, "swagger_types", {})
    payload = {key: _coerce(item, swagger.get(key), models) for key, item in payload.items()}
    from_dict = getattr(oci.util, "from_dict", None)
    if not callable(from_dict):
        return model(**payload)
    try:
        return from_dict(model, payload)
    except (TypeError, ValueError):
        return model(**payload)


def _coerce_arguments(client: Any, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    method = getattr(client, operation)
    models = _models_module(client.__class__)
    signature = inspect.signature(method)
    docs = parameter_docs(method)
    coerced: dict[str, Any] = {}
    for key, value in arguments.items():
        parameter = signature.parameters.get(key)
        type_name = _type_name(parameter.annotation) if parameter and parameter.kind is not inspect.Parameter.VAR_KEYWORD else None
        if type_name is None and key in docs:
            type_name = docs[key][0]
        coerced[key] = _coerce(value, type_name, models)
    return coerced


def _require_compartment_scope(tool: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Give actionable guidance when a required catalog scope is missing."""
    for requirement in compartment_requirements(tool):
        argument = requirement["argument"]
        if requirement["required"] and not arguments.get(argument):
            raise ValueError(
                f"{tool['name']} requires {argument}. Ask the user for a compartment OCID, or for a "
                "root_compartment_id to resolve a supplied compartment name. Do not invent an OCID."
            )


def _validate_compartment_scope(tool: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
    """Reject invalid or inaccessible catalog compartment scopes before dispatch."""
    requirements = compartment_requirements(tool)
    values = {str(arguments[requirement["argument"]]) for requirement in requirements if arguments.get(requirement["argument"])}
    if not values:
        return
    client = identity_bootstrap_client()
    for compartment_id in values:
        try:
            client.get_compartment(compartment_id=compartment_id)
        except oci.exceptions.ServiceError as exc:
            if exc.code == "NotAuthorizedOrNotFound" or exc.status in {403, 404}:
                raise ValueError(
                    f"The supplied compartment OCID {compartment_id} is invalid or inaccessible to the configured "
                    "OCI identity. Ask the user for a valid compartment OCID, or for a root_compartment_id to "
                    "resolve a compartment name."
                ) from exc
            raise RuntimeError(f"Unable to validate compartment OCID {compartment_id}: {exc}") from exc


def invoke_registered_tool(tool: Mapping[str, Any], arguments: dict[str, Any]) -> Any:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    _require_compartment_scope(tool, arguments)
    errors = sorted(Draft202012Validator(dict(tool["inputSchema"])).iter_errors(arguments), key=lambda error: list(error.path))
    if errors:
        raise ValueError(f"Invalid arguments: {errors[0].message}")
    _validate_compartment_scope(tool, arguments)
    client = _client(str(tool["service"]), str(tool["client"]))
    try:
        response = getattr(client, tool["operation"])(**_coerce_arguments(client, str(tool["operation"]), arguments))
    except Exception as exc:
        raise RuntimeError(f"OCI operation {tool['name']} failed: {exc}") from exc
    return serialize_response(response)
