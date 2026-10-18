import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TypeVar

from django.utils import timezone
from django.utils.dates import WEEKDAYS_ABBR
from django.utils.functional import Promise
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from management.activity import Activity, summed
from management.formatting import (
    as_cached,
    as_failed,
    as_input,
    as_output,
    as_percent,
    as_requests,
    as_tokens,
    compact,
    grouped,
)
from management.periods import Step

SPARK_BARS = 12
AXIS_LABELS = 8
HEAT_CLASSES = (
    "bg-base-300",
    "bg-primary/20",
    "bg-primary/40",
    "bg-primary/60",
    "bg-primary/80",
    "bg-primary",
)
HEAT_HOURS = tuple(f"{hour:02d}" if hour % 3 == 0 else "" for hour in range(24))
TOKEN_SERIES = {"c": _("Cached"), "n": _("New input"), "o": _("Output")}
REQUEST_SERIES = {"i": _("OK"), "f": _("Failed")}
K = TypeVar("K")


def scaled(values: Iterable[float], top: float | None = None) -> tuple[int, ...]:
    values = list(values)
    top = max(values, default=0) if top is None else top
    return tuple(round(100 * v / top) if top else 0 for v in values)


@dataclass(frozen=True)
class Tip:
    heading: str
    value: str
    parts: tuple[tuple[str, str | Promise, str], ...] = ()

    def __str__(self) -> str:
        detail = ", ".join(f"{name} {value}" for _, name, value in self.parts)
        return f"{self.heading}: {self.value}" + (f" ({detail})" if detail else "")


@dataclass(frozen=True)
class SparkBar:
    height: int
    tip: Tip


def spark(
    points: Sequence[tuple[str, float]], show: Callable[[float], str]
) -> tuple[SparkBar, ...]:
    return tuple(
        SparkBar(height, Tip(label, show(value)))
        for (label, value), height in zip(points, scaled(v for _, v in points), strict=True)
    )


@dataclass(frozen=True)
class Stat:
    label: str
    value: str
    spark: tuple[SparkBar, ...]


@dataclass(frozen=True)
class Column:
    label: str
    tip: Tip
    segments: tuple[tuple[str, int], ...]
    short: str = ""

    @property
    def height(self) -> int:
        return sum(height for _, height in self.segments)


@dataclass(frozen=True)
class Share:
    name: str
    value: str
    width: int


def chart(
    points: Sequence[tuple[str, Activity]], metric: str, every: int = 1
) -> tuple[Column, ...]:
    values: list[tuple[tuple[str, int], ...]]
    if metric == "requests":
        names, show = REQUEST_SERIES, as_requests
        values = [(("i", a.requests - a.failed), ("f", a.failed)) for _, a in points]
    else:
        names, show = TOKEN_SERIES, as_tokens
        values = [a.segments for _, a in points]
    top = max((sum(n for _, n in segs) for segs in values), default=0)

    def tip(label: str, segs: tuple[tuple[str, int], ...]) -> Tip:
        total = sum(n for _, n in segs)
        parts = tuple((css, names[css], grouped(n)) for css, n in segs) if total else ()
        return Tip(label, show(total), parts)

    return tuple(
        Column(
            label if index % every == 0 else "",
            tip(label, segs),
            tuple((css, round(100 * n / top)) for css, n in segs if n),
        )
        for index, ((label, _), segs) in enumerate(zip(points, values, strict=True))
    )


def columns(
    points: Sequence[tuple[datetime, Activity]], step: Step, metric: str
) -> tuple[Column, ...]:
    local = [timezone.localtime(moment) for moment, _ in points]
    labelled = [(step.format(moment), a) for moment, (_, a) in zip(local, points, strict=True)]
    found = chart(labelled, metric, every=math.ceil(len(points) / AXIS_LABELS) or 1)
    return tuple(
        replace(column, short=step.format(moment, short=True) if column.label else "")
        for column, moment in zip(found, local, strict=True)
    )


def slots(
    points: Sequence[tuple[datetime, Activity]], step: Step
) -> tuple[tuple[str, Activity], ...]:
    size = math.ceil(len(points) / SPARK_BARS) or 1

    def slot(group: Sequence[tuple[datetime, Activity]]) -> tuple[str, Activity]:
        first, last = (step.format(timezone.localtime(m)) for m in (group[0][0], group[-1][0]))
        between = gettext("%(first)s to %(last)s") % {"first": first, "last": last}
        label = first if first == last else between
        return label, summed([a for _, a in group])

    return tuple(slot(points[i : i + size]) for i in range(0, len(points), size))


@dataclass(frozen=True)
class Metric:
    label: Promise
    total: Callable[[Activity], str]
    each: Callable[[Activity], float]
    show: Callable[[float], str]


def stats(points: Sequence[tuple[str, Activity]], metrics: Sequence[Metric]) -> tuple[Stat, ...]:
    total = summed([a for _, a in points])
    return tuple(
        Stat(
            str(metric.label),
            metric.total(total),
            spark([(label, metric.each(a)) for label, a in points], metric.show),
        )
        for metric in metrics
    )


def _failed_percent(a: Activity) -> float:
    return 100 * a.failed / a.requests if a.requests else 0.0


REQUESTS = Metric(_("Requests"), lambda a: grouped(a.requests), lambda a: a.requests, as_requests)
FAILED = Metric(_("Failed"), lambda a: a.failed_share, lambda a: a.failed, as_failed)
OVERVIEW_STATS = (
    Metric(_("Input"), lambda a: compact(a.input), lambda a: a.input, as_input),
    Metric(_("Output"), lambda a: compact(a.output), lambda a: a.output, as_output),
    Metric(_("Cached"), lambda a: compact(a.cached), lambda a: a.cached, as_cached),
    REQUESTS,
    Metric(_("Failed"), lambda a: a.failed_share, _failed_percent, as_percent),
)
WINDOW_STATS = (
    Metric(_("Input tokens"), lambda a: compact(a.input), lambda a: a.input, as_tokens),
    Metric(_("Output tokens"), lambda a: compact(a.output), lambda a: a.output, as_tokens),
    Metric(_("Cache hits"), lambda a: a.cache_hits, lambda a: a.cached, as_cached),
    REQUESTS,
    FAILED,
)
TEAM_STATS = (
    Metric(_("Tokens"), lambda a: compact(a.tokens), lambda a: a.tokens, as_tokens),
    REQUESTS,
    FAILED,
)


def token_widths(activity: Mapping[K, Activity]) -> dict[K, int]:
    return dict(zip(activity, scaled(a.tokens for a in activity.values()), strict=True))


@dataclass(frozen=True)
class HeatCell:
    tip: Tip
    css: str


@dataclass(frozen=True)
class HeatRow:
    day: str
    cells: tuple[HeatCell, ...]


def heat(tokens: Mapping[tuple[int, int], int], weeks: int) -> tuple[HeatRow, ...]:
    average = {slot: count / weeks for slot, count in tokens.items()}
    top = max(average.values(), default=0)
    levels = len(HEAT_CLASSES) - 1

    def cell(day: int, hour: int) -> HeatCell:
        value = average.get((day, hour), 0)
        level = math.ceil(levels * value / top) if value else 0
        when = gettext("%(day)s %(start)s to %(end)s") % {
            "day": WEEKDAYS_ABBR[day - 1],
            "start": f"{hour:02d}:00",
            "end": f"{(hour + 1) % 24:02d}:00",
        }
        per_hour = gettext("%(tokens)s per hour") % {"tokens": as_tokens(value)}
        return HeatCell(Tip(when, per_hour), HEAT_CLASSES[level])

    return tuple(
        HeatRow(str(WEEKDAYS_ABBR[day - 1]), tuple(cell(day, hour) for hour in range(24)))
        for day in range(1, 8)
    )
