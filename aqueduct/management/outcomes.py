from django.db.models import Count, Q
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

FAILED = Q(status_code__gte=400)
STATUSES: tuple[tuple[Promise, str, str, str, Q], ...] = (
    (_("OK"), "2xx", "ok", "st-ok", Q(status_code__lt=400)),
    (_("Rate-limited"), "429", "warning", "st-warn", Q(status_code=429)),
    (
        _("Client error"),
        "4xx",
        "off",
        "st-off",
        FAILED & Q(status_code__lt=500) & ~Q(status_code=429),
    ),
    (_("Server error"), "5xx", "failed", "st-err", Q(status_code__gte=500)),
)
STATUS_COUNTS = {f"s{i}": Count("id", filter=match) for i, (*_, match) in enumerate(STATUSES)}
