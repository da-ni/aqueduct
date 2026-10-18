from functools import wraps
from typing import Any

from django.core.handlers.asgi import ASGIRequest

from gateway import config
from gateway.decorators.types import AsyncView, Decorator, ViewResult
from gateway.response_error import error_response


def require_files_api_client(api: str) -> Decorator:
    def decorator(view_func: AsyncView) -> AsyncView:
        @wraps(view_func)
        async def wrapper(request: ASGIRequest, *args: Any, **kwargs: Any) -> ViewResult:
            try:
                kwargs["client"] = config.get_files_api_client()
            except ValueError:
                return error_response(f"{api} not configured", status=503)
            return await view_func(request, *args, **kwargs)

        return wrapper

    return decorator
