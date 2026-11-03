from django.conf import settings
from django.http import HttpRequest, HttpResponseBase
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView


def is_safe(request: HttpRequest, url: str | None) -> bool:
    return bool(url) and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def safe_next(request: HttpRequest) -> str | None:
    next_url = request.GET.get("next")
    return next_url if is_safe(request, next_url) else None


class SSOTemplateView(TemplateView):
    template_name = "management/login.html"

    def dispatch(self, request, *args, **kwargs) -> HttpResponseBase:
        if request.user.is_authenticated:
            return redirect(safe_next(request) or settings.LOGIN_REDIRECT_URL)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["OIDC_PROVIDER"] = getattr(settings, "OIDC_PROVIDER", "SSO")
        return context
