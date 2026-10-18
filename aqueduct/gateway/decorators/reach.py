from functools import wraps
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.core.handlers.asgi import ASGIRequest
from django.db import models

from gateway.decorators.types import AsyncView, Decorator, ViewResult
from gateway.response_error import error_response


def in_reach(
    model: type[models.Model],
    id_kwarg: str,
    *,
    label: str,
    within: str | None = None,
    inject: str | None = None,
) -> Decorator:
    """Swap the URL's `<name>_id` for the `<name>` object the key reaches, or answer 404.

    `inject` overrides the view argument's name. `within` names an object an outer
    `in_reach` injected that this one must belong to.
    """
    name = inject or id_kwarg.removesuffix("_id")

    def decorator(view_func: AsyncView) -> AsyncView:
        @wraps(view_func)
        async def wrapper(request: ASGIRequest, *args: Any, **kwargs: Any) -> ViewResult:
            reach = models.Q(**{within: kwargs[within]}) if within else kwargs["token"].reach()
            try:
                kwargs[name] = await model._default_manager.aget(reach, id=kwargs.pop(id_kwarg))
            except ObjectDoesNotExist:
                return error_response(f"{label} not found.", param=id_kwarg, status=404)
            return await view_func(request, *args, **kwargs)

        return wrapper

    return decorator
