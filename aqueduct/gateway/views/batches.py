from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from openai import AsyncOpenAI
from openai.types.batch_create_params import BatchCreateParams
from pydantic import TypeAdapter

from gateway.batch_file import unusable_model
from gateway.decorators import (
    catch_router_exceptions,
    in_reach,
    log_request,
    parse_body,
    require_files_api_client,
    token_authenticated,
    tos_accepted,
)
from gateway.response_error import error_response
from gateway.response_type import RawJsonResponse
from management.models import Batch, BatchStatus, FileObject, Token

from .files import sync_batch_file_if_needed


@csrf_exempt
@require_http_methods(["GET", "POST"])
@token_authenticated(token_auth_only=True)
@tos_accepted
@parse_body(TypeAdapter(BatchCreateParams))
@log_request
@catch_router_exceptions
@require_files_api_client("Files API")
async def batches(
    request: ASGIRequest,
    token: Token,
    client: AsyncOpenAI,
    pydantic_model: BatchCreateParams | None = None,
    *args: Any,
    **kwargs: Any,
) -> RawJsonResponse:
    """
    GET /batches - list the batches the key reaches from local DB
    POST /batches - create a new batch on upstream
    """
    if request.method == "GET":
        batch_qs = Batch.objects.filter(token.reach())

        batch_objects = [
            b.model.model_dump()
            async for b in batch_qs.order_by("-created_at").select_related(
                "input_file", "output_file", "error_file"
            )
        ]

        return RawJsonResponse(
            {"object": "list", "data": batch_objects, "has_more": False}, status=200
        )

    # POST /batches

    if pydantic_model is None:
        return error_response("Invalid request body", status=400)

    # Check batch limit before creating batch (for upstream quota management)
    limit = settings.MAX_TEAM_BATCHES if token.service_account else settings.MAX_USER_BATCHES
    active_count = await Batch.objects.filter(
        token.reach(),
        status__in=[BatchStatus.VALIDATING, BatchStatus.IN_PROGRESS, BatchStatus.CANCELLING],
    ).acount()

    if active_count >= limit:
        return error_response(f"Batch limit reached ({limit})", status=403)

    try:
        file_obj = await FileObject.objects.aget(token.reach(), id=pydantic_model["input_file_id"])
    except FileObject.DoesNotExist:
        return error_response("Input file not found.", param="input_file_id", status=404)

    if file_obj.batch_models is None:
        return error_response(
            "Batch file validation failed: upload the input file again with purpose 'batch'",
            status=400,
        )
    excluded_models = await sync_to_async(token.model_exclusion_list)()
    unusable = unusable_model(file_obj.batch_models, excluded_models)
    if unusable:
        return error_response(
            f"Batch file validation failed: Unknown model '{unusable}'", status=400
        )

    # Create batch on upstream using file ID
    remote_batch = await client.batches.create(
        input_file_id=file_obj.id,
        endpoint=pydantic_model["endpoint"],
        completion_window=pydantic_model["completion_window"],
        metadata=pydantic_model.get("metadata"),
    )

    # Create local tracking record with upstream ID
    now = timezone.now()
    batch_obj = Batch(
        id=remote_batch.id,
        completion_window=pydantic_model["completion_window"],
        created_at=int(now.timestamp()),
        endpoint=pydantic_model["endpoint"],
        input_file=file_obj,
        token=token,
        status=remote_batch.status,
        metadata=pydantic_model.get("metadata"),
        expires_at=remote_batch.expires_at,
        request_counts=remote_batch.request_counts.model_dump()
        if remote_batch.request_counts
        else {},
    )
    await batch_obj.asave()

    remote_batch.input_file_id = file_obj.id
    return RawJsonResponse(remote_batch, status=200)


@csrf_exempt
@require_http_methods(["GET"])
@token_authenticated(token_auth_only=True)
@tos_accepted
@log_request
@catch_router_exceptions
@require_files_api_client("Files API")
@in_reach(Batch, "batch_id", label="Batch")
async def batch(
    request: ASGIRequest, token: Token, batch: Batch, client: AsyncOpenAI, *args: Any, **kwargs: Any
) -> RawJsonResponse:
    """
    GET /batches/{batch_id} - retrieve a batch the key reaches from upstream

    When output_file_id or error_file_id exist in the upstream
    response but don't have local FileObject records, create them lazily.
    """
    remote_batch = await batch.areload_from_upstream(client)

    if remote_batch is None:
        return error_response("Batch not found.", param="batch_id", status=404)

    # Create local FileObject records for output/error files if missing
    output_file_obj = await sync_batch_file_if_needed(
        remote_batch.output_file_id, token, client, batch, "output_file"
    )
    error_file_obj = await sync_batch_file_if_needed(
        remote_batch.error_file_id, token, client, batch, "error_file"
    )

    # Ensure file IDs in the response data match our local records
    remote_batch.input_file_id = batch.input_file_id
    if output_file_obj:
        remote_batch.output_file_id = output_file_obj.id
    if error_file_obj:
        remote_batch.error_file_id = error_file_obj.id

    return RawJsonResponse(remote_batch, status=200)


@csrf_exempt
@require_POST
@token_authenticated(token_auth_only=False)
@tos_accepted
@log_request
@catch_router_exceptions
@require_files_api_client("Files API")
@in_reach(Batch, "batch_id", label="Batch")
async def batch_cancel(
    request: ASGIRequest, batch: Batch, client: AsyncOpenAI, *args: Any, **kwargs: Any
) -> RawJsonResponse:
    """POST /batches/{batch_id}/cancel - cancel a batch the key reaches on upstream"""
    remote_batch = await batch.acancel_upstream(client)

    # Ensure file ID in the response data matches our local records
    remote_batch.input_file_id = batch.input_file_id

    return RawJsonResponse(remote_batch, status=200)
