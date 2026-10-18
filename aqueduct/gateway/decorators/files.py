import json
import logging
import sys
from functools import wraps
from typing import TYPE_CHECKING, Any

import litellm
from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest

from gateway.config import get_files_api_client, get_router, get_router_config, resolve_model_alias
from gateway.decorators.types import AsyncView, ViewResult
from gateway.response_error import error_response

if TYPE_CHECKING:
    from management.models import Token

log = logging.getLogger("aqueduct")


def require_files_api_client(view_func: AsyncView) -> AsyncView:
    """Decorator that injects a files API client into the view kwargs, or returns 503.

    Uses late-bound import of get_files_api_client so that tests can
    patch it on the calling module (e.g. gateway.views.vector_stores).
    """

    @wraps(view_func)
    async def wrapper(request: ASGIRequest, *args: Any, **kwargs: Any) -> ViewResult:
        # Look up get_files_api_client from the module where view_func is defined,
        # so tests patching that module's reference will be respected.
        view_module = sys.modules.get(view_func.__module__)
        _get_client = getattr(view_module, "get_files_api_client", get_files_api_client)

        try:
            client = _get_client()
        except ValueError:
            return error_response("Vector Store API not configured", status=503)
        kwargs["client"] = client
        return await view_func(request, *args, **kwargs)

    return wrapper


def _lookup_relay_model_name(model_name: str) -> str | None:
    """Return upstream relay model for a configured deployment, else None."""
    router = get_router()
    if not router:
        return model_name

    deployment: litellm.Deployment | None = router.get_deployment(model_id=model_name)
    if not deployment:
        return None

    litellm_params = getattr(deployment, "litellm_params", None)
    deployment_model = getattr(litellm_params, "model", None)
    if not deployment_model:
        return None

    relay_model: str
    relay_model, _, _, _ = litellm.get_llm_provider(deployment_model)
    return relay_model


def rewrite_batch_file_models(content: bytes, excluded_models: set[str]) -> bytes:
    lines = content.decode("utf-8").splitlines()
    rewritten_lines = []

    for line in lines:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            body = request.get("body", {})
            if "model" in body:
                original_model = body["model"]
                canonical_model = resolve_model_alias(original_model)
                relay_model = _lookup_relay_model_name(canonical_model)
                if relay_model is None or canonical_model in excluded_models:
                    custom_id = request.get("custom_id", "<missing custom_id>")
                    raise ValueError(
                        f"Unknown model '{original_model}' for custom_id '{custom_id}'"
                    )
                body["model"] = relay_model
                request["body"] = body
            if "custom_id" in request:
                # The OpenAI Batch API requires a string custom_id.
                request["custom_id"] = str(request["custom_id"])
            rewritten_lines.append(json.dumps(request, separators=(",", ":")))
        except json.JSONDecodeError:
            # Keep invalid lines as-is (will fail validation anyway)
            rewritten_lines.append(line)

    return ("\n".join(rewritten_lines) + "\n").encode("utf-8")


def check_batch_file_models(content: bytes, excluded_models: set[str]) -> None:
    relays = {
        entry["model_name"]: _lookup_relay_model_name(entry["model_name"])
        for entry in get_router_config()["model_list"]
    }
    allowed = {relay for name, relay in relays.items() if name not in excluded_models}
    for i, line in enumerate(content.decode("utf-8").splitlines()):
        if not line.strip():
            continue
        request = json.loads(line)
        if not isinstance(request, dict):
            raise ValueError(f"Line {i + 1} is not a JSON object")  # noqa: TRY004
        body = request.get("body")
        model = body.get("model") if isinstance(body, dict) else None
        if model is not None and model not in allowed:
            named = [name for name, relay in relays.items() if relay == model]
            custom_id = request.get("custom_id", "<missing custom_id>")
            raise ValueError(
                f"Unknown model '{named[0] if len(named) == 1 else model}' "
                f"for custom_id '{custom_id}'"
            )


def extract_preview(content: bytes, num_lines: int = 10) -> str:
    """Extract first 10 lines from file content for preview."""
    try:
        lines = content.decode("utf-8").splitlines()[:num_lines]
        return "\n".join(lines)
    except UnicodeDecodeError:
        return "[Binary content - no preview available]"


def process_batch_file(view_func: AsyncView) -> AsyncView:

    @wraps(view_func)
    async def wrapper(request: ASGIRequest, *args: Any, **kwargs: Any) -> ViewResult:
        if request.method != "POST":
            return await view_func(request, *args, **kwargs)

        pydantic_model = kwargs.get("pydantic_model")
        if not pydantic_model:
            return await view_func(request, *args, **kwargs)

        uploaded = pydantic_model.get("file")
        purpose = pydantic_model.get("purpose")

        if not uploaded:
            return await view_func(request, *args, **kwargs)

        # Read file content ONCE
        content = uploaded.read()

        if purpose == "batch":
            token: Token | None = kwargs.get("token")
            if not token:
                log.error(
                    "process_batch_file decorator used without @token_authenticated decorator"
                )
                return error_response("Internal server error", status=500)
            excluded_models = set(await sync_to_async(token.model_exclusion_list)())
            kwargs["file_preview"] = extract_preview(content)
            try:
                kwargs["file_content"] = rewrite_batch_file_models(content, excluded_models)
            except ValueError as e:
                return error_response(f"Batch file validation failed: {e!s}", status=400)
        else:
            kwargs["file_content"] = content
            kwargs["file_preview"] = ""

        return await view_func(request, *args, **kwargs)

    return wrapper
