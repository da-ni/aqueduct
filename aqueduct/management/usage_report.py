import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
from django.db.models.functions import Lower, Trunc
from django.utils import timezone
from django.utils.dates import WEEKDAYS_ABBR
from django.utils.formats import date_format
from django.utils.functional import Promise
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from management.key_activity import (
    FAILED,
    MISSING,
    Activity,
    Column,
    Share,
    SparkBar,
    Tip,
    as_cached,
    as_percent,
    as_requests,
    as_tokens,
    chart,
    compact,
    decimal,
    grouped,
    percent,
    spark,
)
from management.models import Org, Request, Token
from management.ownership import RELATED, key_org

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
HEAT_HOURS = tuple(f"{hour:02d}" if hour % 3 == 0 else "" for hour in range(24))
TOKENS = F("input_tokens") + F("output_tokens")
NO_ORG = _("No organization")
APPROXIMATE_MEDIAN = _("Approximate")
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
    short_format: str
    tokens_title: str | Promise
    requests_title: str | Promise

    def floor(self, moment: datetime) -> datetime:
        local = timezone.localtime(moment)
        zeroed: dict[str, Any] = dict.fromkeys(self.zeroed, 0)
        return local.replace(microsecond=0, **zeroed)

    def format(self, moment: datetime, short: bool = False) -> str:
        return date_format(moment, self.short_format if short else self.label_format)


MINUTE = Step(
    "minute",
    timedelta(minutes=1),
    "H:i",
    ("second",),
    "H:i",
    _("Tokens per minute"),
    _("Requests per minute"),
)
HOUR = Step(
    "hour",
    timedelta(hours=1),
    "H:00",
    ("second", "minute"),
    "H",
    _("Tokens per hour"),
    _("Requests per hour"),
)
DAY = Step(
    "day",
    timedelta(days=1),
    "DAY_MONTH_FORMAT",
    ("second", "minute", "hour"),
    "DAY_OF_MONTH_FORMAT",
    _("Tokens per day"),
    _("Requests per day"),
)


class Span(StrEnum):
    HOUR = "1h"
    DAY = "1d"
    WEEK = "1w"
    MONTH = "1m"

    @property
    def label(self) -> str:
        return str(SPANS[self].label)

    @property
    def no_requests(self) -> str:
        return str(SPANS[self].no_requests)

    @classmethod
    def parse(cls, raw: str) -> "Span | None":
        try:
            return cls(raw)
        except ValueError:
            return None


@dataclass(frozen=True)
class SpanSpec:
    label: Promise
    no_requests: Promise
    step: Step
    count: int


DEFAULT_SPAN = Span.DAY
SPANS: Mapping[Span, SpanSpec] = {
    Span.HOUR: SpanSpec(_("1 hour"), _("No requests in the last hour."), MINUTE, 60),
    Span.DAY: SpanSpec(_("24 hours"), _("No requests in the last 24 hours."), HOUR, 24),
    Span.WEEK: SpanSpec(_("7 days"), _("No requests in the last 7 days."), DAY, 7),
    Span.MONTH: SpanSpec(_("30 days"), _("No requests in the last 30 days."), DAY, 30),
}


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    step: Step

    @classmethod
    def last(cls, span: Span, now: datetime) -> "Period":
        spec = SPANS[span]
        end = spec.step.floor(now) + spec.step.size
        return cls(end - spec.count * spec.step.size, end, spec.step)

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
    label: str | Promise
    codes: str
    icon: str
    tone: str
    count: int
    share: str
    href: str


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


class Median(Aggregate):
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
    local = [timezone.localtime(moment) for moment, _ in points]
    labelled = [(step.format(moment), a) for moment, (_, a) in zip(local, points, strict=True)]
    found = chart(labelled, metric, every=math.ceil(len(points) / AXIS_LABELS) or 1)
    return tuple(
        replace(column, short=step.format(moment, short=True) if column.label else "")
        for column, moment in zip(found, local, strict=True)
    )


def duration(ms: int | None) -> str:
    if ms is None:
        return gettext("n/a")
    return f"{grouped(ms)} ms" if ms < SECOND_MS else f"{decimal(ms / SECOND_MS, 1)} s"


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


@dataclass(frozen=True)
class Kpi:
    label: str
    value: str
    spark: tuple[SparkBar, ...]


def _summed(activities: Sequence[Activity]) -> Activity:
    return Activity(**{field: sum(getattr(a, field) for a in activities) for field in TOTALS})


@dataclass(frozen=True)
class Slot:
    label: str
    activity: Activity


def slots(points: Sequence[tuple[datetime, Activity]], step: Step) -> tuple[Slot, ...]:
    size = math.ceil(len(points) / SPARK_BARS) or 1

    def slot(group: Sequence[tuple[datetime, Activity]]) -> Slot:
        first, last = (step.format(timezone.localtime(m)) for m in (group[0][0], group[-1][0]))
        between = gettext("%(first)s to %(last)s") % {"first": first, "last": last}
        label = first if first == last else between
        return Slot(label, _summed([a for _, a in group]))

    return tuple(slot(points[i : i + size]) for i in range(0, len(points), size))


def kpis(spark_slots: Sequence[Slot]) -> tuple[Kpi, ...]:
    now = _summed([s.activity for s in spark_slots])

    def bars_of(
        value: Callable[[Activity], float], show: Callable[[float], str]
    ) -> tuple[SparkBar, ...]:
        return spark([(s.label, value(s.activity)) for s in spark_slots], show)

    def as_input(count: float) -> str:
        return ngettext("%(count)s input token", "%(count)s input tokens", round(count)) % {
            "count": compact(round(count))
        }

    def as_output(count: float) -> str:
        return ngettext("%(count)s output token", "%(count)s output tokens", round(count)) % {
            "count": compact(round(count))
        }

    return (
        Kpi(gettext("Input"), compact(now.input), bars_of(lambda a: a.input, as_input)),
        Kpi(gettext("Output"), compact(now.output), bars_of(lambda a: a.output, as_output)),
        Kpi(gettext("Cached"), compact(now.cached), bars_of(lambda a: a.cached, as_cached)),
        Kpi(gettext("Requests"), grouped(now.requests), bars_of(lambda a: a.requests, as_requests)),
        Kpi(
            gettext("Failed"),
            now.failed_share,
            bars_of(lambda a: 100 * _ratio(a.failed, a.requests), as_percent),
        ),
    )


def statuses(timeline: Series, link: Callable[..., str]) -> tuple[StatusRow, ...]:
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


class Figures:
    activity: Activity

    def _metered(self, value: str) -> str:
        return value if self.activity.tokens else MISSING

    @property
    def input(self) -> str:
        return self._metered(compact(self.activity.input))

    @property
    def cached(self) -> str:
        return self._metered(self.activity.cache_hits)

    @property
    def output(self) -> str:
        return self._metered(compact(self.activity.output))


@dataclass(frozen=True)
class ModelRow(Figures):
    name: str
    kind: str
    activity: Activity
    median: str
    approximate: bool
    share: str
    width: int
    log_url: str


@dataclass(frozen=True)
class ModelUsage:
    activity: Activity
    median_ms: int | None


def model_usage(requests: QuerySet[Request]) -> dict[str, ModelUsage]:
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
    found = {name: u.activity for name, u in usage.items()}
    ranked = sorted(found.items(), key=lambda item: (-item[1].tokens, -item[1].requests, item[0]))
    total = sum(a.tokens for a in found.values())
    busiest = max((a.tokens for a in found.values()), default=0)

    return tuple(
        ModelRow(
            name=name,
            kind=kinds.get(name, ""),
            activity=activity,
            median=MISSING if usage[name].median_ms is None else duration(usage[name].median_ms),
            approximate=approximate and usage[name].median_ms is not None,
            share=percent(activity.tokens, total) if activity.tokens else MISSING,
            width=round(100 * activity.tokens / busiest) if busiest else 0,
            log_url=link(model=name),
        )
        for name, activity in ranked
    )


@dataclass(frozen=True)
class KeyShare(Figures):
    name: str
    detail: str
    href: str | None
    activity: Activity
    share: str
    width: int


def _widths(values: Iterable[int], top: int | None = None) -> list[int]:
    values = list(values)
    top = max(values, default=0) if top is None else top
    return [round(100 * v / top) if top else 0 for v in values]


def _figures(
    activities: Sequence[Activity], total: int, busiest: int | None = None
) -> list[tuple[Activity, str, int]]:
    return [
        (activity, percent(activity.tokens, total) if activity.tokens else MISSING, width)
        for activity, width in zip(
            activities, _widths((a.tokens for a in activities), busiest), strict=True
        )
    ]


def _activity(row: Mapping[str, Any]) -> Activity:
    return Activity(**{name: row[name] for name in TOTALS})


class KeyOrder(StrEnum):
    TOKENS = "tokens"
    REQUESTS = "requests"
    NAME = "name"

    @property
    def ascending(self) -> bool:
        return self is KeyOrder.NAME

    @property
    def ordering(self) -> tuple[str | Subquery, ...]:
        if self is KeyOrder.NAME:
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


def _key_row(row: Mapping[str, Any]) -> KeyRow:
    return row["token_id"], _activity(row)


def _by_key(requests: QuerySet[Request]) -> QuerySet[Request, dict[str, Any]]:
    return requests.values("token_id").annotate(**TOTALS, tokens=Sum(TOKENS, default=0))


@dataclass(frozen=True)
class KeyTotals:
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
    return _by_key(requests).order_by(*order.ordering)


def top_keys(requests: QuerySet[Request], limit: int = TOP_KEYS) -> list[KeyRow]:
    return [_key_row(row) for row in ranked_keys(requests)[:limit]]


def ranked_totals(
    totals: Mapping[int, Activity], order: KeyOrder, tokens: QuerySet[Token]
) -> list[KeyRow]:
    if order is KeyOrder.NAME:
        named = [
            token_id
            for token_id in tokens.order_by(Lower("name"), "id").values_list("id", flat=True)
            if token_id in totals
        ]
        gone = sorted(set(totals) - set(named))
        return [(token_id, totals[token_id]) for token_id in [*named, *gone]]
    if order is KeyOrder.REQUESTS:
        return sorted(totals.items(), key=lambda r: (-r[1].requests, -r[1].tokens, r[0]))
    return sorted(totals.items(), key=lambda r: (-r[1].tokens, -r[1].requests, r[0]))


@dataclass(frozen=True)
class CountedKeys:
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
    tokens = Token.objects.select_related(*RELATED).in_bulk([token_id for token_id, _ in rows])
    rows = [row for row in rows if row[0] in tokens]
    keys = [describe(tokens[token_id]) for token_id, _ in rows]
    figures = _figures([activity for _, activity in rows], total, busiest)
    return tuple(
        KeyShare(key.name, key.note, key.href, *figure)
        for key, figure in zip(keys, figures, strict=True)
    )


def user_id_shares(requests: QuerySet[Request], total: int) -> tuple[KeyShare, ...]:
    rows = list(
        requests.values("user_id")
        .annotate(**TOTALS, tokens=Sum(TOKENS, default=0))
        .order_by("-tokens", "-requests", "user_id")[:TOP_USER_IDS]
    )
    figures = _figures([_activity(row) for row in rows], total)
    return tuple(
        KeyShare(row["user_id"] or gettext("Not sent"), "", None, *figure)
        for row, figure in zip(rows, figures, strict=True)
    )


def org_tokens(requests: QuerySet[Request]) -> dict[int | None, int]:
    return {
        row["org_id"]: row["tokens"]
        for row in requests.values(org_id=key_org("token__"))
        .annotate(tokens=Sum(TOKENS, default=0))
        .order_by()
    }


def org_shares(tokens: Mapping[int | None, int]) -> tuple[Share, ...]:
    orgs = Org.objects.in_bulk([org_id for org_id in tokens if org_id is not None])
    no_org = str(NO_ORG)
    rows = sorted(
        (
            (orgs[org_id].name if org_id in orgs else no_org, count)
            for org_id, count in tokens.items()
        ),
        key=lambda row: (-row[1], row[0] == no_org, row[0]),
    )
    return tuple(
        Share(name, compact(count), width)
        for (name, count), width in zip(rows, _widths(count for _, count in rows), strict=True)
    )
