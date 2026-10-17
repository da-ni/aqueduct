"""Figures for the Overview page, computed from a queryset of requests the viewer may see."""

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from django.db.models import (
    Aggregate,
    Count,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce, Lower, Trunc
from django.utils import timezone

from management.key_activity import (
    FAILED,
    Activity,
    Column,
    Share,
    SparkBar,
    Tip,
    as_cached,
    as_requests,
    as_tokens,
    chart,
    compact,
    percent,
    served,
    spark,
)
from management.models import Org, Request, Token
from management.models.token import of_org

if TYPE_CHECKING:
    from management.request_log import Key

SPARK_BARS = 12
SECOND_MS = 1000
AXIS_LABELS = 8
TOP_KEYS = 10
TOP_USER_IDS = 25
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
NO_ORG = "No organization"
APPROXIMATE_MEDIAN = "Approximate: within about 5%, or 1 ms for very fast responses"
ORG = Coalesce("token__service_account__team__org", "token__user__profile__org")
"""A service account's key counts for its team's org, a personal key for its owner's org."""
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
        zeroed: dict[str, Any] = dict.fromkeys(self.zeroed, 0)
        return local.replace(microsecond=0, **zeroed)


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
        """The last `span` before `now`."""
        label, step, count = SPANS[span]
        end = step.floor(now) + step.size
        return cls(end - count * step.size, end, step, label)

    @property
    def buckets(self) -> list[datetime]:
        moment, found = self.step.floor(self.start), []
        while moment < self.end:
            found.append(moment)
            moment += self.step.size
        return found

    def of(self, requests: QuerySet[Request]) -> QuerySet[Request]:
        return requests.filter(timestamp__gte=self.start, timestamp__lt=self.end)


@dataclass(frozen=True)
class StatusRow:
    label: str
    codes: str
    icon: str
    tone: str
    count: int
    share: str
    href: str


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
    ("Model or provider error", "5xx", "failed", "st-err", Q(status_code__gte=500)),
)


class Median(Aggregate):
    """The lower median of the non-null values, one per group.

    SQLite has no ordered-set aggregates, so there the values are collected and the median is
    picked in Python, which is fine at development scale."""

    function = "PERCENTILE_DISC"
    template = "%(function)s(0.5) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = IntegerField()

    def as_sqlite(self, compiler: Any, connection: Any, **extra_context: Any) -> tuple[str, Any]:
        return super().as_sql(
            compiler,
            connection,
            function="GROUP_CONCAT",
            template="%(function)s(%(expressions)s)",
            **extra_context,
        )

    def get_db_converters(self, connection: Any) -> list[Callable[..., Any]]:
        if connection.vendor == "sqlite":
            return [_lower_median]
        return super().get_db_converters(connection)


def _lower_median(value: str | None, *_: object) -> int | None:
    if not value:
        return None
    ordered = sorted(int(v) for v in value.split(","))
    return ordered[(len(ordered) - 1) // 2]


MEDIAN = Median("response_time_ms")
STATUS_COUNTS = {f"s{i}": Count("id", filter=match) for i, (*_, match) in enumerate(STATUSES)}


@dataclass(frozen=True)
class Series:
    """Activity per bucket of a period, empty buckets included, and the requests per status
    group of `STATUSES` over the whole period."""

    points: list[tuple[datetime, Activity]]
    status_counts: tuple[int, ...]

    @property
    def total(self) -> Activity:
        return _summed([a for _, a in self.points])


def series(requests: QuerySet[Request], period: Period) -> Series:
    rows = (
        period.of(requests)
        .annotate(bucket=Trunc("timestamp", period.step.kind))
        .values("bucket")
        .annotate(**TOTALS, **STATUS_COUNTS)
        .order_by()
    )
    found, status_counts = {}, [0] * len(STATUSES)
    for row in rows:
        bucket = row.pop("bucket")
        for i in range(len(STATUSES)):
            status_counts[i] += row.pop(f"s{i}")
        found[bucket] = Activity(**row)
    return Series(
        [(bucket, found.get(bucket, Activity())) for bucket in period.buckets], tuple(status_counts)
    )


def columns(
    points: Sequence[tuple[datetime, Activity]], step: Step, metric: str
) -> tuple[Column, ...]:
    labelled = [(timezone.localtime(moment).strftime(step.label_format), a) for moment, a in points]
    return chart(labelled, metric, every=math.ceil(len(points) / AXIS_LABELS) or 1)


def duration(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return f"{ms:,} ms" if ms < SECOND_MS else f"{ms / SECOND_MS:.1f} s"


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    spark: tuple[SparkBar, ...]


def _share(ratio: float) -> str:
    return f"{round(100 * ratio)}%"


def _summed(activities: Sequence[Activity]) -> Activity:
    return Activity(**{field: sum(getattr(a, field) for a in activities) for field in TOTALS})


@dataclass(frozen=True)
class Slot:
    """The buckets one spark bar covers."""

    label: str
    activity: Activity


def slots(points: Sequence[tuple[datetime, Activity]], step: Step) -> tuple[Slot, ...]:
    """The period's buckets grouped into at most `SPARK_BARS` spark bars."""
    size = math.ceil(len(points) / SPARK_BARS) or 1

    def slot(group: Sequence[tuple[datetime, Activity]]) -> Slot:
        first, last = (
            timezone.localtime(m).strftime(step.label_format) for m in (group[0][0], group[-1][0])
        )
        return Slot(
            first if first == last else f"{first} to {last}", _summed([a for _, a in group])
        )

    return tuple(slot(points[i : i + size]) for i in range(0, len(points), size))


def kpis(spark_slots: Sequence[Slot]) -> tuple[Kpi, ...]:
    """The headline figures of the period, each with a spark bar per slot."""
    now = _summed([s.activity for s in spark_slots])

    def bars_of(
        value: Callable[[Activity], float], show: Callable[[float], str]
    ) -> tuple[SparkBar, ...]:
        return spark([(s.label, value(s.activity)) for s in spark_slots], show)

    return (
        Kpi("Input from cache", compact(now.cached), bars_of(lambda a: a.cached, as_cached)),
        Kpi(
            "New input",
            compact(now.input - now.cached),
            bars_of(
                lambda a: a.input - a.cached, lambda n: f"{compact(round(n))} new input tokens"
            ),
        ),
        Kpi(
            "Output",
            compact(now.output),
            bars_of(lambda a: a.output, lambda n: f"{compact(round(n))} output tokens"),
        ),
        Kpi("Requests", f"{now.requests:,}", bars_of(lambda a: a.requests, as_requests)),
        Kpi("Failed", now.failed_share, bars_of(lambda a: _ratio(a.failed, a.requests), _share)),
    )


def statuses(timeline: Series, link: Callable[..., str]) -> tuple[StatusRow, ...]:
    """Requests per status group. `link(status=codes)` opens the log on that group."""
    total = timeline.total.requests
    return tuple(
        StatusRow(label, codes, icon, tone, count, percent(count, total), link(status=codes))
        for (label, codes, icon, tone, _), count in zip(
            STATUSES, timeline.status_counts, strict=True
        )
    )


@dataclass(frozen=True)
class HeatCell:
    tip: Tip
    css: str


@dataclass(frozen=True)
class HeatRow:
    day: str
    cells: tuple[HeatCell, ...]


def org_requests(org: Org | None) -> QuerySet[Request]:
    """Every request of the keys of `org`, whoever owns them, limited to the models the router
    serves. `None` means every org."""
    if org is None:
        return served(Request.objects.all())
    tokens = Token.objects.filter(of_org(org))
    return served(Request.objects.filter(token__in=tokens))


def heat(tokens: Mapping[tuple[int, int], int], weeks: int) -> tuple[HeatRow, ...]:
    """Average tokens per hour for each weekday and hour, from the `tokens` per ISO weekday and
    hour of day over `weeks` weeks."""
    average = {slot: count / weeks for slot, count in tokens.items()}
    top = max(average.values(), default=0)
    levels = len(HEAT_CLASSES) - 1

    def cell(day: int, hour: int) -> HeatCell:
        value = average.get((day, hour), 0)
        level = math.ceil(levels * value / top) if value else 0
        when = f"{WEEKDAYS[day - 1]} {hour:02d}:00 to {(hour + 1) % 24:02d}:00"
        return HeatCell(Tip(when, f"{as_tokens(value)} per hour"), HEAT_CLASSES[level])

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
    approximate: bool
    """Whether `median` is approximate."""
    share: str
    width: int
    log_url: str


@dataclass(frozen=True)
class ModelUsage:
    activity: Activity
    median_ms: int | None


def model_usage(requests: QuerySet[Request]) -> dict[str, ModelUsage]:
    """Activity and lower median response time per model, for the models with requests."""
    found = {}
    for row in requests.values("model").annotate(**TOTALS, median=MEDIAN).order_by():
        model, median = row.pop("model"), row.pop("median")
        found[model] = ModelUsage(Activity(**row), median)
    return found


def model_rows(
    usage: Mapping[str, ModelUsage],
    kinds: Mapping[str, str],
    link: Callable[..., str],
    *,
    approximate: bool = False,
) -> tuple[ModelRow, ...]:
    """Every model with requests, busiest first. `link(model=name)` opens the log on one.
    `approximate` marks the medians as such."""
    found = {name: u.activity for name, u in usage.items()}
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
            median=duration(usage[name].median_ms),
            approximate=approximate and usage[name].median_ms is not None,
            share=percent(activity.tokens, total),
            width=round(100 * activity.tokens / busiest) if busiest else 0,
            log_url=link(model=name),
        )
        for name, activity in ranked
    )


@dataclass(frozen=True)
class KeyShare:
    """One row of the Overview's Top keys or By user ID table, laid out like `ModelRow`."""

    name: str
    detail: str
    href: str | None
    activity: Activity
    share: str
    width: int

    @property
    def input(self) -> str:
        return compact(self.activity.input)

    @property
    def output(self) -> str:
        return compact(self.activity.output)


def _widths(values: Iterable[int], top: int | None = None) -> list[int]:
    """Bar widths in percent of `top`, by default of the largest value."""
    values = list(values)
    top = max(values, default=0) if top is None else top
    return [round(100 * v / top) if top else 0 for v in values]


def _figures(
    activities: Sequence[Activity], total: int, busiest: int | None = None
) -> list[tuple[Activity, str, int]]:
    """Activity, token share, and bar width of each row, as `model_rows` shows them. `total` is
    the period's token count, and `busiest` the token count of a full-width bar, by default the
    first row's."""
    return [
        (activity, percent(activity.tokens, total), width)
        for activity, width in zip(
            activities, _widths((a.tokens for a in activities), busiest), strict=True
        )
    ]


def _activity(row: Mapping[str, Any]) -> Activity:
    return Activity(**{name: row[name] for name in TOTALS})


class KeyOrder(StrEnum):
    """How the All keys page sorts its rows."""

    TOKENS = "tokens"
    REQUESTS = "requests"
    NAME = "name"

    @property
    def label(self) -> str:
        return self.value.capitalize()

    @property
    def ordering(self) -> tuple[str | Subquery, ...]:
        if self is KeyOrder.NAME:
            # A join on the token would sort every request by name before grouping. The
            # subquery runs once per key, after grouping by `token_id`.
            name = Token.objects.filter(pk=OuterRef("token_id")).values(lower=Lower("name"))
            return (Subquery(name), "token_id")
        if self is KeyOrder.REQUESTS:
            return ("-requests", "-tokens", "token_id")
        return ("-tokens", "-requests", "token_id")

    @classmethod
    def parse(cls, raw: str) -> "KeyOrder | None":
        try:
            return cls(raw)
        except ValueError:
            return None


KeyRow = tuple[int, Activity]
"""A key's id and its activity in a period."""


def _key_row(row: Mapping[str, Any]) -> KeyRow:
    return row["token_id"], _activity(row)


def _by_key(requests: QuerySet[Request]) -> QuerySet[Request, dict[str, Any]]:
    return requests.values("token_id").annotate(**TOTALS, tokens=Sum(TOKENS, default=0))


@dataclass(frozen=True)
class KeyTotals:
    """The keys with requests in a period, the token count of the busiest one, and the tokens
    of all of them."""

    keys: int
    busiest: int
    tokens: int

    @classmethod
    def of(cls, totals: Mapping[int, Activity]) -> "KeyTotals":
        tokens = [a.tokens for a in totals.values()]
        return cls(len(tokens), max(tokens, default=0), sum(tokens))


def key_totals(requests: QuerySet[Request]) -> KeyTotals:
    found = (
        _by_key(requests)
        .order_by()
        .aggregate(n=Count("token_id"), top=Max("tokens", default=0), all=Sum("tokens", default=0))
    )
    return KeyTotals(found["n"], found["top"], found["all"])


def ranked_keys(
    requests: QuerySet[Request], order: KeyOrder = KeyOrder.TOKENS
) -> QuerySet[Request, dict[str, Any]]:
    """One row of totals per key, in `order`."""
    return _by_key(requests).order_by(*order.ordering)


def top_keys(requests: QuerySet[Request]) -> list[KeyRow]:
    return [_key_row(row) for row in ranked_keys(requests)[:TOP_KEYS]]


def ranked_totals(
    totals: Mapping[int, Activity], order: KeyOrder, tokens: QuerySet[Token]
) -> list[KeyRow]:
    """`ranked_keys` of activity already counted per key. Sorting by name orders `tokens`, which
    hold the keys of `totals`, in the database, so that names collate as in `ranked_keys`."""
    if order is KeyOrder.NAME:
        named = [
            token_id
            for token_id in tokens.order_by(Lower("name"), "id").values_list("id", flat=True)
            if token_id in totals
        ]
        gone = sorted(set(totals) - set(named))  # deleted since the count
        return [(token_id, totals[token_id]) for token_id in [*named, *gone]]
    if order is KeyOrder.REQUESTS:
        return sorted(totals.items(), key=lambda r: (-r[1].requests, -r[1].tokens, r[0]))
    return sorted(totals.items(), key=lambda r: (-r[1].tokens, -r[1].requests, r[0]))


@dataclass(frozen=True)
class CountedKeys:
    """`ranked_keys` whose number is already known, so that a `Paginator` slices them without
    counting them again."""

    rows: QuerySet[Request, dict[str, Any]]
    size: int

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, page: slice) -> list[KeyRow]:
        return [_key_row(row) for row in self.rows[page]]


def key_shares(
    rows: Sequence[KeyRow],
    total: int,
    describe: Callable[[Token], "Key"],
    busiest: int | None = None,
) -> tuple[KeyShare, ...]:
    """The ranked `rows` as table rows. `describe` names a key and links it where the viewer may
    open it. Bars are as wide as `busiest` tokens, by default the first row's."""
    tokens = Token.objects.select_related(
        "user__profile__org", "service_account__team__org"
    ).in_bulk([token_id for token_id, _ in rows])
    rows = [row for row in rows if row[0] in tokens]  # deleted since the grouping
    keys = [describe(tokens[token_id]) for token_id, _ in rows]
    figures = _figures([activity for _, activity in rows], total, busiest)
    return tuple(
        KeyShare(key.name, key.note, key.href, *figure)
        for key, figure in zip(keys, figures, strict=True)
    )


def user_id_shares(requests: QuerySet[Request], total: int) -> tuple[KeyShare, ...]:
    """Tokens per `user` value the client sent, for one key."""
    rows = list(
        requests.values("user_id")
        .annotate(**TOTALS, tokens=Sum(TOKENS, default=0))
        .order_by("-tokens", "-requests", "user_id")[:TOP_USER_IDS]
    )
    figures = _figures([_activity(row) for row in rows], total)
    return tuple(
        KeyShare(row["user_id"] or "Not sent", "", None, *figure)
        for row, figure in zip(rows, figures, strict=True)
    )


def org_tokens(requests: QuerySet[Request]) -> dict[int | None, int]:
    """Tokens per org id, `None` for keys of no org."""
    return {
        row["org_id"]: row["tokens"]
        for row in requests.values(org_id=ORG).annotate(tokens=Sum(TOKENS, default=0)).order_by()
    }


def org_shares(tokens: Mapping[int | None, int]) -> tuple[Share, ...]:
    """The `tokens` of each org, busiest first."""
    orgs = Org.objects.in_bulk([org_id for org_id in tokens if org_id is not None])
    rows = sorted(
        (
            (orgs[org_id].name if org_id in orgs else NO_ORG, count)
            for org_id, count in tokens.items()
        ),
        key=lambda row: (-row[1], row[0] == NO_ORG, row[0]),
    )
    return tuple(
        Share(name, compact(count), width)
        for (name, count), width in zip(rows, _widths(count for _, count in rows), strict=True)
    )
