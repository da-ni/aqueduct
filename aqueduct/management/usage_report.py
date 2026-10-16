"""Figures for the Overview page, computed from a queryset of requests the viewer may see."""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from django.db.models import Count, F, Q, QuerySet, Sum
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, Trunc
from django.urls import reverse
from django.utils import timezone

from management.key_activity import (
    FAILED,
    Activity,
    Column,
    Share,
    chart,
    compact,
    heights,
    percent,
)
from management.models import Request

SPARK_BARS = 12
SECOND_MS = 1000
AXIS_LABELS = 8
TOP_KEYS = 25
HEAT_CLASSES = (
    "bg-base-300",
    "bg-primary/20",
    "bg-primary/40",
    "bg-primary/60",
    "bg-primary/80",
    "bg-primary",
)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HEAT_HOURS = tuple(f"{hour:02d}" if hour % 3 == 0 else "" for hour in range(24))
TOKENS = F("input_tokens") + F("output_tokens")
TOTALS = {
    "requests": Count("id"),
    "failed": Count("id", filter=FAILED),
    "input": Sum("input_tokens", default=0),
    "cached": Sum("cached_input_tokens", default=0),
    "output": Sum("output_tokens", default=0),
}


@dataclass(frozen=True)
class Step:
    kind: str
    size: timedelta
    label_format: str
    zeroed: tuple[str, ...]

    def floor(self, moment: datetime) -> datetime:
        local = timezone.localtime(moment)
        return local.replace(microsecond=0, **dict.fromkeys(self.zeroed, 0))


MINUTE = Step("minute", timedelta(minutes=1), "%H:%M", ("second",))
HOUR = Step("hour", timedelta(hours=1), "%H:00", ("second", "minute"))
DAY = Step("day", timedelta(days=1), "%-d %b", ("second", "minute", "hour"))


class Span(StrEnum):
    HOUR = "1h"
    DAY = "1d"
    WEEK = "1w"
    MONTH = "1m"

    @property
    def label(self) -> str:
        return SPANS[self][0]

    @classmethod
    def parse(cls, raw: str) -> "Span | None":
        try:
            return cls(raw)
        except ValueError:
            return None


DEFAULT_SPAN = Span.WEEK
SPANS: Mapping[Span, tuple[str, Step, int]] = {
    Span.HOUR: ("1 hour", MINUTE, 60),
    Span.DAY: ("24 hours", HOUR, 24),
    Span.WEEK: ("7 days", DAY, 7),
    Span.MONTH: ("30 days", DAY, 30),
}


@dataclass(frozen=True)
class Period:
    """Requests with `start <= timestamp < end`, charted in buckets of `step`."""

    start: datetime
    end: datetime
    step: Step
    label: str

    @classmethod
    def last(cls, span: Span, now: datetime) -> "Period":
        label, step, count = SPANS[span]
        end = step.floor(now) + step.size
        return cls(end - count * step.size, end, step, label)

    @classmethod
    def between(cls, start: datetime, end: datetime) -> "Period":
        length = end - start
        step = (
            MINUTE if length <= timedelta(hours=2) else HOUR if length <= timedelta(days=2) else DAY
        )
        return cls(start, end, step, "custom range")

    def previous(self, now: datetime) -> "Period":
        """The same stretch one period earlier, cut where this period's elapsed time ends."""
        length = self.end - self.start
        return replace(self, start=self.start - length, end=min(self.end, now) - length)

    @property
    def buckets(self) -> list[datetime]:
        moment, found = self.step.floor(self.start), []
        while moment < self.end:
            found.append(moment)
            moment += self.step.size
        return found

    def of(self, requests: QuerySet[Request]) -> QuerySet[Request]:
        return requests.filter(timestamp__gte=self.start, timestamp__lt=self.end)


def totals(requests: QuerySet[Request]) -> Activity:
    return Activity(**requests.aggregate(**TOTALS))


def series(requests: QuerySet[Request], period: Period) -> list[tuple[datetime, Activity]]:
    """Activity per bucket of the period, empty buckets included."""
    rows = (
        period.of(requests)
        .annotate(bucket=Trunc("timestamp", period.step.kind))
        .values("bucket")
        .annotate(**TOTALS)
        .order_by()
    )
    found = {row.pop("bucket"): Activity(**row) for row in rows}
    return [(bucket, found.get(bucket, Activity())) for bucket in period.buckets]


def columns(
    points: Sequence[tuple[datetime, Activity]], step: Step, metric: str
) -> tuple[Column, ...]:
    labelled = [(timezone.localtime(moment).strftime(step.label_format), a) for moment, a in points]
    return chart(labelled, metric, every=math.ceil(len(points) / AXIS_LABELS) or 1)


def median_ms(requests: QuerySet[Request]) -> int | None:
    timed = requests.exclude(response_time_ms=None)
    count = timed.count()
    if not count:
        return None
    return timed.order_by("response_time_ms").values_list("response_time_ms", flat=True)[count // 2]


def duration(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return f"{ms:,} ms" if ms < SECOND_MS else f"{ms / SECOND_MS:.1f} s"


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _relative(now: float, before: float) -> str | None:
    return f"{round(100 * (now - before) / before):+d}%" if before else None


def _points(now: float, before: float) -> str:
    return f"{100 * (now - before):+.1f}".removesuffix(".0") + " pts"


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    change: str | None
    spark: tuple[int, ...]


def _summed(activities: Sequence[Activity]) -> Activity:
    return Activity(**{field: sum(getattr(a, field) for a in activities) for field in TOTALS})


def kpis(
    points: Sequence[tuple[datetime, Activity]],
    median: int | None,
    before: Activity | None,
    before_median: int | None,
) -> tuple[Kpi, ...]:
    """The headline figures, each with its change against the previous period when known."""
    activities = [a for _, a in points]
    size = math.ceil(len(activities) / SPARK_BARS) or 1
    bars = [_summed(activities[i : i + size]) for i in range(0, len(activities), size)]
    now = _summed(activities)
    known = before if before is not None and before.requests else None

    def relative(field: str) -> str | None:
        return _relative(getattr(now, field), getattr(known, field)) if known else None

    def points(part: str, whole: str) -> str | None:
        if not known:
            return None
        return _points(
            _ratio(getattr(now, part), getattr(now, whole)),
            _ratio(getattr(known, part), getattr(known, whole)),
        )

    return (
        Kpi(
            "Input tokens", compact(now.input), relative("input"), heights([a.input for a in bars])
        ),
        Kpi(
            "Output tokens",
            compact(now.output),
            relative("output"),
            heights([a.output for a in bars]),
        ),
        Kpi(
            "Cache hits",
            now.cache_hits,
            points("cached", "input"),
            heights([_ratio(a.cached, a.input) for a in bars]),
        ),
        Kpi(
            "Requests",
            f"{now.requests:,}",
            relative("requests"),
            heights([a.requests for a in bars]),
        ),
        Kpi(
            "Failed",
            now.failed_share,
            points("failed", "requests"),
            heights([_ratio(a.failed, a.requests) for a in bars]),
        ),
        Kpi(
            "Median response",
            duration(median),
            _relative(median, before_median) if known and median and before_median else None,
            (),
        ),
    )


@dataclass(frozen=True)
class StatusRow:
    label: str
    codes: str
    icon: str
    tone: str
    count: int
    share: str


STATUSES = (
    ("Answered", "2xx", "ok", "st-ok", Q(status_code__lt=400)),
    ("Rate-limited", "429", "warning", "st-warn", Q(status_code=429)),
    (
        "Other client errors",
        "4xx",
        "off",
        "st-off",
        FAILED & Q(status_code__lt=500) & ~Q(status_code=429),
    ),
    ("Model or provider failed", "5xx", "failed", "st-err", Q(status_code__gte=500)),
)


def statuses(requests: QuerySet[Request], total: int) -> tuple[StatusRow, ...]:
    counts = requests.aggregate(
        **{f"s{i}": Count("id", filter=match) for i, (*_, match) in enumerate(STATUSES)}
    )
    return tuple(
        StatusRow(label, codes, icon, tone, counts[f"s{i}"], percent(counts[f"s{i}"], total))
        for i, (label, codes, icon, tone, _) in enumerate(STATUSES)
    )


@dataclass(frozen=True)
class HeatCell:
    title: str
    css: str


@dataclass(frozen=True)
class HeatRow:
    day: str
    cells: tuple[HeatCell, ...]


def heat(requests: QuerySet[Request], now: datetime, weeks: int) -> tuple[HeatRow, ...]:
    """Average tokens per hour for each weekday and hour over the last `weeks` weeks."""
    rows = (
        requests.filter(timestamp__gte=now - timedelta(weeks=weeks))
        .annotate(weekday=ExtractIsoWeekDay("timestamp"), hour=ExtractHour("timestamp"))
        .values("weekday", "hour")
        .annotate(tokens=Sum(TOKENS, default=0))
        .order_by()
    )
    average = {(row["weekday"], row["hour"]): row["tokens"] / weeks for row in rows}
    top = max(average.values(), default=0)
    levels = len(HEAT_CLASSES) - 1

    def cell(day: int, hour: int) -> HeatCell:
        value = average.get((day, hour), 0)
        level = math.ceil(levels * value / top) if value else 0
        title = f"{WEEKDAYS[day - 1]} {hour:02d}:00: {compact(round(value))} tokens per hour"
        return HeatCell(title, HEAT_CLASSES[level])

    return tuple(
        HeatRow(name, tuple(cell(day, hour) for hour in range(24)))
        for day, name in enumerate(WEEKDAYS, start=1)
    )


@dataclass(frozen=True)
class ModelRow:
    name: str
    kind: str
    activity: Activity
    input: str
    output: str
    median: str
    share: str
    width: int


def model_rows(requests: QuerySet[Request], kinds: Mapping[str, str]) -> tuple[ModelRow, ...]:
    """Every model with requests, busiest first."""
    found = {
        row.pop("model"): Activity(**row)
        for row in requests.values("model").annotate(**TOTALS).order_by()
    }
    ranked = sorted(found.items(), key=lambda item: (-item[1].tokens, -item[1].requests, item[0]))
    total = sum(a.tokens for a in found.values())
    busiest = max((a.tokens for a in found.values()), default=0)
    return tuple(
        ModelRow(
            name=name,
            kind=kinds.get(name, ""),
            activity=activity,
            input=compact(activity.input),
            output=compact(activity.output),
            median=duration(median_ms(requests.filter(model=name))),
            share=percent(activity.tokens, total),
            width=round(100 * activity.tokens / busiest) if busiest else 0,
        )
        for name, activity in ranked
    )


@dataclass(frozen=True)
class KeyShare:
    name: str
    detail: str
    href: str | None
    value: str
    width: int


def _widths(values: Iterable[int]) -> list[int]:
    values = list(values)
    top = max(values, default=0)
    return [round(100 * v / top) if top else 0 for v in values]


def key_shares(
    requests: QuerySet[Request], viewer_id: int, team_ids: set[int], with_org: bool
) -> tuple[KeyShare, ...]:
    """The keys that used the most tokens. A key links to the page the viewer may open for it."""
    rows = list(
        requests.values(
            "token_id",
            "token__name",
            "token__user_id",
            "token__user__email",
            "token__service_account__team_id",
            "token__service_account__team__name",
            "token__user__profile__org__name",
        )
        .annotate(tokens=Sum(TOKENS, default=0), requests=Count("id"))
        .order_by("-tokens", "-requests", "token__name")[:TOP_KEYS]
    )

    def describe(row: dict) -> tuple[str, str | None]:
        team_id = row["token__service_account__team_id"]
        if team_id is not None:
            detail = f"Service account · {row['token__service_account__team__name']}"
            href = reverse("team", kwargs={"id": team_id}) if team_id in team_ids else None
        else:
            detail = f"Personal · {row['token__user__email']}"
            own = row["token__user_id"] == viewer_id
            href = reverse("token_detail", kwargs={"id": row["token_id"]}) if own else None
        if with_org:
            detail += f" · {row['token__user__profile__org__name']}"
        return detail, href

    return tuple(
        KeyShare(row["token__name"], *describe(row), compact(row["tokens"]), width)
        for row, width in zip(rows, _widths(r["tokens"] for r in rows), strict=True)
    )


def user_id_shares(requests: QuerySet[Request]) -> tuple[KeyShare, ...]:
    """Tokens per `user` value the client sent, for one key."""
    rows = list(
        requests.values("user_id")
        .annotate(tokens=Sum(TOKENS, default=0), requests=Count("id"))
        .order_by("-tokens", "-requests", "user_id")[:TOP_KEYS]
    )
    return tuple(
        KeyShare(
            row["user_id"] or "Not sent",
            f"{row['requests']:,} requests",
            None,
            compact(row["tokens"]),
            width,
        )
        for row, width in zip(rows, _widths(r["tokens"] for r in rows), strict=True)
    )


def org_shares(requests: QuerySet[Request]) -> tuple[Share, ...]:
    rows = list(
        requests.values(name=F("token__user__profile__org__name"))
        .annotate(tokens=Sum(TOKENS, default=0))
        .order_by("-tokens", "name")
    )
    return tuple(
        Share(row["name"], compact(row["tokens"]), width)
        for row, width in zip(rows, _widths(r["tokens"] for r in rows), strict=True)
    )
