import io
from datetime import timedelta
from typing import Any, Literal, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.db.models import Sum
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from openai import AsyncOpenAI
from openai.types.file_create_params import FileCreateParams as OpenAIFileCreateParams
from pydantic import BaseModel, ConfigDict, TypeAdapter

from gateway.batch_file import parse_batch_file
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
from management.models import Batch, FileObject, Token


class FilesCreateParams(BaseModel):
    file: bytes
    purpose: Literal["assistants", "batch", "user_data"]
    # IO[bytes] requires arbitrary_types_allowed for model settings
    model_config = ConfigDict(arbitrary_types_allowed=True)


def calculate_expires_at(remote_expires_at: int | None) -> int:
    """Calculate local expiry timestamp, using earlier of local or upstream expiry."""
    now = timezone.now()
    expiry_days = settings.AQUEDUCT_FILES_API_EXPIRY_DAYS
    local_expires_at = int((now + timedelta(days=expiry_days)).timestamp())

    if remote_expires_at and remote_expires_at < local_expires_at:
        return remote_expires_at
    return local_expires_at


async def sync_batch_file_if_needed(
    remote_file_id: str | None,
    token: Token,
    client: AsyncOpenAI,
    batch_obj: Optional["Batch"] = None,
    field_name: Literal["output_file", "error_file"] = "output_file",
) -> FileObject | None:
    """
    Ensure a local FileObject record exists for a batch output/error file.

    When a batch completes, the upstream provider creates output_file_id and
    error_file_id. These files need local FileObject records for:
    1. Access control (a key reaches only the files in its reach)
    2. Consistent ID mapping (users use Aqueduct IDs, not remote IDs)

    This function is called when retrieving a batch to lazily create
    FileObject records for any output/error files that don't exist locally.
    """
    if not remote_file_id:
        return None

    try:
        return await FileObject.objects.aget(token.reach(), id=remote_file_id)
    except FileObject.DoesNotExist:
        pass

    # Fetch file metadata from upstream
    try:
        remote_file = await client.files.retrieve(remote_file_id)
    except Exception:
        # File may have been deleted upstream, skip
        return None

    # Use get_or_create to prevent duplicate records from concurrent requests
    # (e.g., two GET /batches/{id} requests polling the same completed batch).
    local_expires_at = calculate_expires_at(remote_file.expires_at)

    file_obj, created = await FileObject.objects.aget_or_create(
        id=remote_file.id,
        defaults={
            "token": token,
            "bytes": remote_file.bytes,
            "filename": remote_file.filename,
            "created_at": remote_file.created_at,
            "purpose": remote_file.purpose,
            "expires_at": local_expires_at,
            "preview": "",
            "upstream_url": settings.AQUEDUCT_FILES_API_URL,
        },
    )

    if not created and not await FileObject.objects.filter(token.reach(), id=file_obj.id).aexists():
        return None

    # Update batch record with linked file if batch_obj provided
    if batch_obj:
        setattr(batch_obj, field_name, file_obj)
        await batch_obj.asave(update_fields=[field_name])

    return file_obj


@csrf_exempt
@require_http_methods(["GET", "POST"])
@token_authenticated(token_auth_only=True)
@tos_accepted
@parse_body(model=TypeAdapter(FilesCreateParams))
@log_request
@catch_router_exceptions
@require_files_api_client("Files API")
async def files(
    request: ASGIRequest,
    token: Token,
    client: AsyncOpenAI,
    pydantic_model: OpenAIFileCreateParams | None = None,
    *args: Any,
    **kwargs: Any,
) -> RawJsonResponse:
    if request.method == "GET":
        qs = (
            FileObject.objects.filter(token.reach()).order_by("-created_at").select_related("token")
        )

        file_models = [file_obj.model async for file_obj in qs]
        return RawJsonResponse(
            {"object": "list", "data": file_models, "has_more": False}, status=200
        )

    # POST /files
    if pydantic_model is None:
        return error_response("Invalid request body", status=400)

    uploaded = pydantic_model["file"]
    purpose = pydantic_model["purpose"]

    filename = getattr(uploaded, "name", None)
    if not isinstance(uploaded, io.BytesIO) or not isinstance(filename, str):
        return error_response("Invalid file upload", status=400)

    file_content = uploaded.read()
    batch_file = None
    if purpose == "batch":
        if not filename.endswith(".jsonl"):
            return error_response(
                "Only .jsonl files are currently supported for purpose 'batch'.", status=400
            )
        excluded_models = await sync_to_async(token.model_exclusion_list)()
        try:
            batch_file = await sync_to_async(parse_batch_file, thread_sensitive=False)(
                file_content, excluded_models
            )
        except ValueError as e:
            return error_response(f"Batch file validation failed: {e!s}", status=400)
        file_content = batch_file.content

    # Enforce per-token total storage limit
    sum_res = await FileObject.objects.filter(token.reach()).aaggregate(sum_bytes=Sum("bytes"))
    current_total = sum_res.get("sum_bytes") or 0
    max_total_bytes = settings.AQUEDUCT_FILES_API_MAX_PER_TOKEN_SIZE_MB * 1024 * 1024
    if current_total + len(file_content) > max_total_bytes:
        return error_response(
            f"Total files size exceeds "
            f"{settings.AQUEDUCT_FILES_API_MAX_PER_TOKEN_SIZE_MB}MB limit.",
            status=413,
        )

    # Create a file-like object for the OpenAI client
    file_tuple = (filename, io.BytesIO(file_content))

    # Upload to upstream using OpenAI client
    remote_file = await client.files.create(file=file_tuple, purpose=purpose)

    # Create local tracking record with preview
    local_expires_at = calculate_expires_at(remote_file.expires_at)

    file_obj = FileObject(
        id=remote_file.id,
        token=token,
        bytes=len(file_content),
        filename=filename,
        created_at=int(timezone.now().timestamp()),
        purpose=purpose,
        expires_at=local_expires_at,
        preview=batch_file.preview if batch_file else "",
        batch_models=sorted(batch_file.models) if batch_file else None,
        upstream_url=settings.AQUEDUCT_FILES_API_URL or "",
    )
    await file_obj.asave()

    # Return response with upstream ID
    return RawJsonResponse(file_obj.model, status=200)


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
@token_authenticated(token_auth_only=True)
@tos_accepted
@log_request
@catch_router_exceptions
@require_files_api_client("Files API")
@in_reach(FileObject, "file_id", label="File", inject="file_obj")
async def file(
    request: ASGIRequest, file_obj: FileObject, client: AsyncOpenAI, *args: Any, **kwargs: Any
) -> RawJsonResponse:
    """Retrieve or delete a file the key reaches; never falls back to upstream."""
    if request.method == "GET":
        remote_file = await file_obj.areload_from_upstream(client)

        if remote_file is None:
            return error_response("File not found upstream.", param="file_id", status=404)

        # Return response with upstream ID (same as file_obj.id)
        return RawJsonResponse(remote_file, status=200)

    await file_obj.adelete_upstream(client)

    deleted_id = file_obj.id
    await file_obj.adelete()

    response_data = {"id": deleted_id, "object": "file", "deleted": True}
    return RawJsonResponse(response_data, status=200)


@csrf_exempt
@require_GET
@token_authenticated(token_auth_only=True)
@tos_accepted
@log_request
@catch_router_exceptions
@require_files_api_client("Files API")
@in_reach(FileObject, "file_id", label="File", inject="file_obj")
async def file_content(
    request: ASGIRequest, file_obj: FileObject, client: AsyncOpenAI, *args: Any, **kwargs: Any
) -> HttpResponse | RawJsonResponse:
    """Retrieve the content of a file the key reaches; never falls back to upstream."""
    response = await client.files.content(file_obj.id)

    return HttpResponse(response.content, content_type="application/octet-stream", status=200)
