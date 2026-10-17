from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseBase, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView
from tos import views as tos_views  # type: ignore[import-untyped]


def is_safe(request: HttpRequest, url: str | None) -> bool:
    return bool(url) and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def safe_next(request: HttpRequest) -> str | None:
    """The `next` parameter when it points into this site, else `None`."""
    next_url = request.GET.get("next")
    return next_url if is_safe(request, next_url) else None


@never_cache
def check_tos(request: HttpRequest) -> HttpResponseBase:
    """django-tos's terms prompt, whose own check of `next` lets `http:evil.example` through
    after the user accepts."""
    response = tos_views.check_tos(request)
    next_url = request.POST.get(REDIRECT_FIELD_NAME, request.GET.get(REDIRECT_FIELD_NAME))
    if isinstance(response, HttpResponseRedirect) and not is_safe(request, next_url):
        return redirect(settings.LOGIN_REDIRECT_URL)
    return response


class SSOTemplateView(TemplateView):
    template_name = "management/login.html"

    def dispatch(self, request, *args, **kwargs) -> HttpResponseBase:
        if request.user.is_authenticated:
            return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["OIDC_PROVIDER"] = getattr(settings, "OIDC_PROVIDER", "SSO")
        context["next"] = safe_next(self.request)
        return context


class AdminSSOView(SSOTemplateView):
    """The Django admin's login page. A signed-in user who is not staff gets a 403, since
    sending them back to the admin would bounce them here again."""

    def dispatch(self, request, *args, **kwargs) -> HttpResponseBase:
        if request.user.is_authenticated and not request.user.is_staff:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
