from datetime import date
from decimal import Decimal

from django.utils.formats import date_format, get_format, number_format
from django.utils.translation import gettext, ngettext, pgettext, pgettext_lazy
from django.utils.translation import gettext_lazy as _

MISSING = "—"
NO_ORG = _("No organization")
SECOND_MS = 1000
_STEP = 1000
_SCALES = (
    pgettext_lazy("compact number", "%(number)sK"),
    pgettext_lazy("compact number", "%(number)sM"),
    pgettext_lazy("compact number", "%(number)sB"),
    pgettext_lazy("compact number", "%(number)sT"),
)


def grouped(count: int) -> str:
    return number_format(count, force_grouping=True)


def decimal(value: float, places: int) -> str:
    return number_format(Decimal(f"{value:.{places}f}"))


def compact(count: int) -> str:
    magnitude, scale = float(abs(count)), None
    for next_scale in _SCALES:
        if float(f"{magnitude:.1f}") < _STEP:
            break
        magnitude, scale = magnitude / _STEP, next_scale
    digits = decimal(magnitude, 1).removesuffix(f"{get_format('DECIMAL_SEPARATOR')}0")
    number = ("-" if count < 0 else "") + digits
    return number if scale is None else scale % {"number": number}


def as_percent(number: float) -> str:
    return pgettext("percent", "%(number)s%%") % {"number": grouped(round(number))}


def percent(part: int, whole: int) -> str:
    return as_percent(100 * part / whole) if whole else MISSING


def as_tokens(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s token", "%(count)s tokens", n) % {"count": compact(n)}


def as_input(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s input token", "%(count)s input tokens", n) % {"count": compact(n)}


def as_output(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s output token", "%(count)s output tokens", n) % {"count": compact(n)}


def as_cached(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s cached token", "%(count)s cached tokens", n) % {"count": compact(n)}


def as_requests(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s request", "%(count)s requests", n) % {"count": grouped(n)}


def as_failed(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s failed", "%(count)s failed", n) % {"count": grouped(n)}


def duration(ms: int | None) -> str:
    if ms is None:
        return gettext("n/a")
    return f"{grouped(ms)} ms" if ms < SECOND_MS else f"{decimal(ms / SECOND_MS, 1)} s"


def day_label(day: date) -> str:
    return date_format(day, "DAY_MONTH_FORMAT")
