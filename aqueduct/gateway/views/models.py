from typing import Any

from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from gateway.config import model_entries, model_info_with_context
from gateway.decorators import log_request, token_authenticated, tos_accepted
from gateway.response_type import RawJsonResponse
from management.models import Request, Token

MODEL_CREATION_TIMESTAMP = int(timezone.now().timestamp())


def _model_group_info_entry(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_group": model["model_name"],
        "model_name": model["model_name"],
        "model_info": model_info_with_context(model),
    }


@csrf_exempt
@require_GET
@token_authenticated(token_auth_only=True)
@tos_accepted
@log_request
async def models(
    request: ASGIRequest, token: Token, request_log: Request, *args: Any, **kwargs: Any
) -> RawJsonResponse:
    excluded_models = await sync_to_async(token.model_exclusion_list)()

    return RawJsonResponse(
        data={
            "data": [
                {
                    "id": name,
                    "object": "model",
                    "created": MODEL_CREATION_TIMESTAMP,
                    "owned_by": "aqueduct",
                }
                for name in model_entries()
                if name not in excluded_models
            ],
            "object": "list",
        }
    )


@csrf_exempt
@require_GET
@token_authenticated(token_auth_only=True)
@tos_accepted
@log_request
async def model_group_info(
    request: ASGIRequest, token: Token, request_log: Request, *args: Any, **kwargs: Any
) -> RawJsonResponse:
    """LiteLLM-style rich model metadata endpoint.

    Returns one entry per configured model (JSON array) so that LiteLLM-aware
    clients — which probe /model_group/info before falling back to
    /v1/models — read token limits and capabilities directly from the gateway
    instead of guessing from bundled model catalogs.
    """
    excluded_models = await sync_to_async(token.model_exclusion_list)()

    return RawJsonResponse(
        data=[
            _model_group_info_entry(model)
            for name, model in model_entries().items()
            if name not in excluded_models
        ]
    )
