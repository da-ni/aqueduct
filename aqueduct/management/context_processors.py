from django.conf import settings as django_settings
from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from management.nav import build_shell


def settings(_) -> dict:
    """
    Adds EXTRA_NAV_LINKS from settings to the template context.
    """
    return {"settings": django_settings}


def shell(request: HttpRequest) -> dict[str, object]:
    return {"shell": SimpleLazyObject(lambda: build_shell(request))}
