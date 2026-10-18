from typing import Any

from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.db.models import Count, Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from openai import AsyncOpenAI
from openai.types import VectorStore as OpenAIVectorStore
from openai.types import VectorStoreSearchParams
from openai.types.vector_store import FileCounts as VectorStoreFileCounts
from openai.types.vector_store_create_params import VectorStoreCreateParams
from openai.types.vector_store_update_params import VectorStoreUpdateParams
from pydantic import TypeAdapter

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
from management.models import Token, VectorStore


@csrf_exempt
@require_http_methods(["GET", "POST"])
@token_authenticated(token_auth_only=True)
@tos_accepted
@parse_body(model=TypeAdapter(VectorStoreCreateParams))
@log_request
@catch_router_exceptions
@require_files_api_client("Vector Store API")
async def vector_stores(
    request: ASGIRequest,
    token: Token,
    client: AsyncOpenAI,
    pydantic_model: VectorStoreCreateParams | None = None,
    *args: Any,
    **kwargs: Any,
) -> RawJsonResponse:
    """
    GET /v1/vector_stores - List vector stores
    POST /v1/vector_stores - Create vector store
    """
    if request.method == "GET":
        vector_stores_qs = VectorStore.objects.filter(token.reach())

        vector_stores_qs = (
            vector_stores_qs.order_by("-created_at")
            .select_related("token")
            .annotate(
                file_count_total=Count("files"),
                file_count_completed=Count("files", filter=Q(files__status="completed")),
                file_count_failed=Count("files", filter=Q(files__status="failed")),
                file_count_in_progress=Count("files", filter=Q(files__status="in_progress")),
                file_count_cancelled=Count("files", filter=Q(files__status="cancelled")),
            )
        )

        return RawJsonResponse(
            {
                "object": "list",
                "data": [
                    OpenAIVectorStore(
                        id=vs.id,
                        object="vector_store",
                        name=vs.name,
                        status=vs.status,  # type: ignore[arg-type]
                        usage_bytes=vs.usage_bytes,
                        created_at=vs.created_at,
                        metadata=vs.metadata,
                        expires_after=vs.expires_after,
                        file_counts=VectorStoreFileCounts(
                            total=getattr(vs, "file_count_total", 0),
                            completed=getattr(vs, "file_count_completed", 0),
                            failed=getattr(vs, "file_count_failed", 0),
                            in_progress=getattr(vs, "file_count_in_progress", 0),
                            cancelled=getattr(vs, "file_count_cancelled", 0),
                        ),
                        last_active_at=vs.last_active_at,
                    )
                    async for vs in vector_stores_qs
                ],
                "has_more": False,
            },
            status=200,
        )

    # POST /v1/vector_stores - Create vector store
    if not pydantic_model:
        return error_response("Missing required parameter: name", param="name", status=400)
    name = pydantic_model.get("name")

    if not name:
        return error_response("Missing required parameter: name", param="name", status=400)

    # Check user/team limits before creating
    limit = (
        settings.MAX_TEAM_VECTOR_STORES
        if token.service_account
        else settings.MAX_USER_VECTOR_STORES
    )
    active_count = await VectorStore.objects.filter(token.reach()).acount()

    if active_count >= limit:
        return error_response(f"Vector store limit reached ({limit})", status=403)

    # Create on upstream
    create_kwargs: dict[str, Any] = {"name": name}
    if pydantic_model.get("expires_after"):
        create_kwargs["expires_after"] = pydantic_model["expires_after"]
    if pydantic_model.get("chunking_strategy"):
        create_kwargs["chunking_strategy"] = pydantic_model["chunking_strategy"]
    if pydantic_model.get("metadata"):
        create_kwargs["metadata"] = pydantic_model["metadata"]

    remote_vs = await client.vector_stores.create(**create_kwargs)

    # Create local record with upstream ID
    now = timezone.now()
    vs_obj = VectorStore(
        id=remote_vs.id,
        token=token,
        name=name,
        status=remote_vs.status or "completed",
        usage_bytes=getattr(remote_vs, "usage_bytes", 0),
        created_at=int(now.timestamp()),
        expires_after=pydantic_model.get("expires_after"),
        chunking_strategy=pydantic_model.get("chunking_strategy"),
        metadata=pydantic_model.get("metadata"),
        upstream_url=settings.AQUEDUCT_FILES_API_URL or "",
    )
    await vs_obj.asave()

    # Return upstream response directly (ID already matches)

    return RawJsonResponse(remote_vs, status=200)


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
@token_authenticated(token_auth_only=True)
@tos_accepted
@parse_body(model=TypeAdapter(VectorStoreUpdateParams))
@log_request
@catch_router_exceptions
@require_files_api_client("Vector Store API")
@in_reach(VectorStore, "vector_store_id", label="Vector store")
async def vector_store(
    request: ASGIRequest,
    vector_store: VectorStore,
    client: AsyncOpenAI,
    pydantic_model: VectorStoreUpdateParams | None = None,
    *args: Any,
    **kwargs: Any,
) -> RawJsonResponse:
    """
    GET /v1/vector_stores/{vector_store_id} - Retrieve vector store
    POST /v1/vector_stores/{vector_store_id} - Modify vector store
    DELETE /v1/vector_stores/{vector_store_id} - Delete vector store
    """
    if request.method == "GET":
        # Retrieve from upstream and sync status
        remote_vs = await vector_store.areload_from_upstream(client)

        if not remote_vs:
            return error_response("Vector store not found.", param="vector_store_id", status=404)

        # Return upstream response directly (ID already matches)
        return RawJsonResponse(remote_vs, status=200)

    if request.method == "POST":
        # Modify vector store
        if not pydantic_model:
            return error_response("No fields to update", status=400)

        modify_kwargs: dict[str, Any] = {}
        if pydantic_model.get("name"):
            modify_kwargs["name"] = pydantic_model["name"]
        if pydantic_model.get("expires_after"):
            modify_kwargs["expires_after"] = pydantic_model["expires_after"]
        if pydantic_model.get("metadata"):
            modify_kwargs["metadata"] = pydantic_model["metadata"]

        if modify_kwargs:
            remote_vs = await client.vector_stores.update(vector_store.id, **modify_kwargs)

            # Update local record
            for field, value in modify_kwargs.items():
                setattr(vector_store, field, value)
            vector_store.status = remote_vs.status
            vector_store.usage_bytes = remote_vs.usage_bytes
            await vector_store.asave()

            # Return upstream response directly (ID already matches)
            return RawJsonResponse(remote_vs, status=200)

        # No changes requested, return current state
        remote_vs = await vector_store.areload_from_upstream(client)
        if not remote_vs:
            return error_response("Vector store not found.", param="vector_store_id", status=404)
        return RawJsonResponse(remote_vs, status=200)

    await vector_store.adelete_upstream(client)

    # Capture ID before delete (Django sets pk to None after delete)
    deleted_id = vector_store.id

    # Delete local record
    await vector_store.adelete()

    # Return with upstream ID
    return RawJsonResponse(
        {"id": deleted_id, "object": "vector_store.deleted", "deleted": True}, status=200
    )


@csrf_exempt
@require_POST
@token_authenticated(token_auth_only=True)
@tos_accepted
@parse_body(model=TypeAdapter(VectorStoreSearchParams))
@log_request
@catch_router_exceptions
@require_files_api_client("Vector Store API")
@in_reach(VectorStore, "vector_store_id", label="Vector store")
async def vector_store_search(
    request: ASGIRequest,
    vector_store: VectorStore,
    client: AsyncOpenAI,
    pydantic_model: VectorStoreSearchParams,
    *args: Any,
    **kwargs: Any,
) -> RawJsonResponse:
    """
    POST /v1/vector_stores/{vector_store_id}/search - Search vector store
    """
    search_results = await client.vector_stores.search(
        vector_store_id=vector_store.id, **pydantic_model
    )

    # Return upstream response directly (IDs already match)
    return RawJsonResponse(search_results, status=200)
