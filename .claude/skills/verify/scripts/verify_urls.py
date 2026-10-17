"""The dev overlay's URLs plus a password-free sign-in for any seeded account.

The mock SSO at /dev-login/ only knows one account per group, and team roles need
more accounts than that. This route exists only on verification instances.
"""

from aqueduct_dev_urls import urlpatterns as dev_urlpatterns
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.shortcuts import redirect, resolve_url
from django.urls import path
from django.utils.http import url_has_allowed_host_and_scheme


def verify_login(request: HttpRequest) -> HttpResponse:
    user = get_user_model().objects.filter(username=request.GET.get("username", "")).first()
    if user is None:
        return HttpResponseNotFound("unknown username", content_type="text/plain")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    target = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = resolve_url(settings.LOGIN_REDIRECT_URL)
    return redirect(target)


urlpatterns = [path("verify-login/", verify_login), *dev_urlpatterns]
