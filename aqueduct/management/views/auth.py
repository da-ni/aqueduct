from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponseBase, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_noop
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView
from tos import views as tos_views

from management.models import UserProfile


def is_safe(request: HttpRequest, url: str | None) -> bool:
    return bool(url) and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def safe_next(request: HttpRequest) -> str | None:
    next_url = request.GET.get("next")
    return next_url if is_safe(request, next_url) else None


# django-tos ships no German, so its message is translated in this project's catalog.
gettext_noop("You cannot login without agreeing to the terms of this site.")


@never_cache
def check_tos(request: HttpRequest) -> HttpResponseBase:
    """django-tos checks `next` only for `//`, so `http:evil.example` passes its check."""
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
    def dispatch(self, request, *args, **kwargs) -> HttpResponseBase:
        if request.user.is_authenticated and not request.user.is_staff:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def remember_language(response: HttpResponseBase, language: str) -> None:
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


class OIDCCallbackView(OIDCAuthenticationCallbackView):
    def login_success(self) -> HttpResponseBase:
        response = super().login_success()
        profile = UserProfile.objects.filter(user=self.user).first()
        if profile and profile.language:
            remember_language(response, profile.language)
        return response
