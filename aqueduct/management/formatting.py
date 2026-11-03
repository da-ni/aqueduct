from decimal import ROUND_HALF_UP, Decimal

from django.utils.formats import get_format, number_format
from django.utils.translation import gettext, pgettext, pgettext_lazy

MISSING = "—"
SECOND_MS = 1000
_STEP = 1000
_SCALES = (
    pgettext_lazy("compact number", "%(number)sK"),
    pgettext_lazy("compact number", "%(number)sM"),
    pgettext_lazy("compact number", "%(number)sB"),
    pgettext_lazy("compact number", "%(number)sT"),
)


def _rounded(value: float | Decimal, places: int) -> Decimal:
    # Round the number as written, half up, so 1.15 shows as 1.2; adding 0 drops a negative zero.
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP) + 0


def grouped(count: int) -> str:
    return number_format(count, force_grouping=True)


def decimal(value: float | Decimal, places: int) -> str:
    return number_format(_rounded(value, places))


def compact(count: int) -> str:
    magnitude, scale = Decimal(abs(count)), None
    for next_scale in _SCALES:
        if _rounded(magnitude, 1) < _STEP:
            break
        magnitude, scale = magnitude / _STEP, next_scale
    digits = decimal(magnitude, 1).removesuffix(f"{get_format('DECIMAL_SEPARATOR')}0")
    number = ("-" if count < 0 else "") + digits
    return number if scale is None else scale % {"number": number}


def as_percent(number: float) -> str:
    return pgettext("percent", "%(number)s%%") % {"number": grouped(int(_rounded(number, 0)))}


def percent(part: int, whole: int) -> str:
    return as_percent(100 * part / whole) if whole else MISSING


def duration(ms: int | None) -> str:
    if ms is None:
        return gettext("n/a")
    return f"{grouped(ms)} ms" if ms < SECOND_MS else f"{decimal(Decimal(ms) / SECOND_MS, 1)} s"
