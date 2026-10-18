import math
import operator
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache, reduce
from itertools import accumulate
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import Case, Count, Expression, F, IntegerField, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Cast, ExtractHour, ExtractIsoWeekDay, Floor, Ln, Trunc
from django.utils import timezone

from gateway.config import model_names
from management.access import EveryOrg, OrgWide, WholeOrg, requests_of
from management.activity import TOKENS, TOTALS, Activity, ModelUsage, Series, served
from management.models import (
    KeyUsageWindow,
    Org,
    OrgLatencyHour,
    OrgUsageHour,
    Request,
    Token,
    UsageRollupState,
)
from management.models.usage_rollup import HOURLY
from management.outcomes import FAILED, OUTCOME_COUNTS, Outcome
from management.ownership import key_org
from management.periods import HOUR, Period, Span

SETTLE = timedelta(minutes=15)
REWRITE = timedelta(hours=24)
KEY_WINDOWS: Mapping[int, Span] = {7: Span.WEEK, 30: Span.MONTH}
CURRENT = timedelta(hours=2)

BUCKETS_PER_DOUBLING = 8
ZERO_BUCKET = -1
BUCKET = Case(
    When(response_time_ms=0, then=Value(ZERO_BUCKET)),
    default=Cast(
        Floor(Ln("response_time_ms") * Value(BUCKETS_PER_DOUBLING / math.log(2))), IntegerField()
    ),
)

_COUNT_OUTCOMES = {o.column: Count("id", filter=o.matches) for o in Outcome} | {
    "unfinished": Count("id", filter=Q(status_code__isnull=True))
}
_SUM_TOKENS = {
    field: Sum(field, default=0)
    for field in ("input_tokens", "cached_input_tokens", "output_tokens")
}

_FAILED_COLUMNS = reduce(operator.add, (F(o.column) for o in Outcome if o.failed))
_HOUR_TOTALS = {
    "requests": Sum(F(Outcome.OK.column) + _FAILED_COLUMNS + F("unfinished"), default=0),
    "failed": Sum(_FAILED_COLUMNS, default=0),
    "input": Sum("input_tokens", default=0),
    "cached": Sum("cached_input_tokens", default=0),
    "output": Sum("output_tokens", default=0),
}
_HOUR_OUTCOME_COUNTS = {o.name: Sum(o.column, default=0) for o in Outcome}


Row = dict[str, Any]
Grouped = dict[tuple[Any, ...], Counter[str]]


def floor_hour(moment: datetime) -> datetime:
    """In UTC: adding a timedelta to a zoned datetime is wall-clock arithmetic."""
    return HOUR.floor(moment).astimezone(UTC)


def _ceil_hour(moment: datetime) -> datetime:
    floor = floor_hour(moment)
    return floor if floor == moment else floor + HOUR.size


@cache
def steady_hours(zone: str) -> bool:
    """Lord Howe Island's daylight saving time moves its clocks by 30 minutes."""
    tz = ZoneInfo(zone)
    year = timezone.now().year
    shifts = {
        (datetime(year, month, 1, tzinfo=tz).utcoffset() or timedelta()) % HOUR.size
        for month in range(1, 13)
    }
    return len(shifts) == 1


def _local_hour() -> Expression:
    shift = (timezone.localtime().utcoffset() or timedelta()) % HOUR.size
    if not shift:
        return Trunc("timestamp", "hour", tzinfo=UTC)
    return Trunc(F("timestamp") - shift, "hour", tzinfo=UTC) + shift


def refresh(now: datetime | None = None, *, full: bool = False) -> None:
    if not steady_hours(settings.TIME_ZONE):
        return
    sealed = floor_hour((now or timezone.now()) - SETTLE)
    with transaction.atomic():
        state = _claimed()
        stale = full or state.dirty or state.time_zone != settings.TIME_ZONE
        start = None if stale or state.sealed_until is None else state.sealed_until
        if start is not None:
            start = min(start, sealed - REWRITE)
        hours = Q() if start is None else Q(hour__gte=start)
        OrgUsageHour.objects.filter(hours).delete()
        OrgLatencyHour.objects.filter(hours).delete()

        recounted = Request.objects.filter(timestamp__lt=sealed)
        if start is not None:
            recounted = recounted.filter(timestamp__gte=start)
        _count_hours(recounted)
        UsageRollupState.objects.filter(name=HOURLY).update(
            sealed_until=sealed,
            time_zone=settings.TIME_ZONE,
            dirty=Case(When(changes=state.changes, then=Value(False)), default=Value(True)),
        )


def prune(cutoff: datetime) -> None:
    """Call after deleting the requests before `cutoff`."""
    if not steady_hours(settings.TIME_ZONE):
        return
    boundary = floor_hour(cutoff)
    expired = Q(hour__lt=boundary + HOUR.size)
    with transaction.atomic():
        state = _claimed()
        OrgUsageHour.objects.filter(expired).delete()
        OrgLatencyHour.objects.filter(expired).delete()
        if state.sealed_until is not None:
            end = min(boundary + HOUR.size, state.sealed_until)
            _count_hours(Request.objects.filter(timestamp__gte=boundary, timestamp__lt=end))


def _count_hours(requests: QuerySet[Request]) -> None:
    requests = requests.order_by()
    by_hour = {"hour": _local_hour(), "org_id": key_org("token__")}
    _insert(
        OrgUsageHour, requests.values("model", **by_hour).annotate(**_COUNT_OUTCOMES, **_SUM_TOKENS)
    )
    _insert(
        OrgLatencyHour,
        requests.filter(response_time_ms__isnull=False)
        .values("model", **by_hour, bucket=BUCKET)
        .annotate(requests=Count("id")),
    )


def refresh_key_windows(now: datetime | None = None) -> None:
    now = now or timezone.now()
    end = floor_hour(now - SETTLE)
    for days, span in KEY_WINDOWS.items():
        start = Period.last(span, now).start
        with transaction.atomic():
            _lock(f"keys-{days}d")
            KeyUsageWindow.objects.filter(days=days).delete()
            _insert(
                KeyUsageWindow,
                Request.objects.filter(timestamp__gte=start, timestamp__lt=end)
                .values("token_id", "model")
                .annotate(requests=Count("id"), failed=Count("id", filter=FAILED), **_SUM_TOKENS)
                .order_by(),
                days=days,
                window_start=start,
                sealed_until=end,
            )


def _lock(name: str) -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [name])


def _claimed() -> UsageRollupState:
    _lock(HOURLY)
    return UsageRollupState.objects.get_or_create(
        name=HOURLY, defaults={"dirty": True, "time_zone": settings.TIME_ZONE}
    )[0]


def _insert(table: type[models.Model], rows: QuerySet[Any, Row], **fixed: object) -> None:
    names = [*fixed, *rows.query.values_select, *rows.query.annotation_select]
    quote = connection.ops.quote_name
    fields = {field.attname: field for field in table._meta.concrete_fields}
    columns = ", ".join(quote(fields[name].column) for name in names)
    picked = ", ".join(["%s"] * len(fixed) + [quote(name) for name in names[len(fixed) :]])
    prepared = [fields[name].get_db_prep_value(value, connection) for name, value in fixed.items()]
    select, params = rows.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {quote(table._meta.db_table)} ({columns}) "  # noqa: S608
            f"SELECT {picked} FROM ({select}) grouped",
            [*prepared, *params],
        )


def org_series(
    extent: OrgWide,
    model: str | None,
    period: Period,
    sealed: "Sealed | None" = None,
    requests: QuerySet[Request] | None = None,
) -> Series:
    found = _gather(
        extent,
        requests,
        model,
        period.start,
        period.end,
        ("bucket",),
        OrgUsageHour,
        lambda rows: rows.values(bucket=Trunc("hour", period.step.kind)).annotate(
            **_HOUR_TOTALS, **_HOUR_OUTCOME_COUNTS
        ),
        lambda rows: rows.values(bucket=Trunc("timestamp", period.step.kind)).annotate(
            **TOTALS, **OUTCOME_COUNTS
        ),
        sealed if period.step.size >= HOUR.size else Sealed(None),
    )
    return Series(
        [(bucket, _activity(found.get((bucket,), Counter()))) for bucket in period.buckets],
        {o: sum(c[o.name] for c in found.values()) for o in Outcome},
    )


def org_models(
    extent: OrgWide,
    model: str | None,
    period: Period,
    sealed: "Sealed | None" = None,
    requests: QuerySet[Request] | None = None,
) -> dict[str, ModelUsage]:
    if requests is None:
        requests = served(requests_of(extent))
    args = (extent, requests, model, period.start, period.end)
    totals = _gather(
        *args,
        ("model",),
        OrgUsageHour,
        lambda rows: rows.values("model").annotate(**_HOUR_TOTALS),
        lambda rows: rows.values("model").annotate(**TOTALS),
        sealed,
    )
    latency = _gather(
        *args,
        ("model", "bucket"),
        OrgLatencyHour,
        lambda rows: rows.values("model", "bucket").annotate(n=Sum("requests")),
        lambda rows: (
            rows.filter(response_time_ms__isnull=False)
            .values("model", bucket=BUCKET)
            .annotate(n=Count("id"))
        ),
        sealed,
    )
    buckets: dict[str, dict[int, int]] = defaultdict(dict)
    for (name, bucket), counts in latency.items():
        buckets[name][bucket] = counts["n"]
    return {
        name: ModelUsage(_activity(counts), approximate_median(buckets[name]))
        for (name,), counts in totals.items()
    }


def org_tokens(
    model: str | None, period: Period, sealed: "Sealed | None" = None
) -> dict[int | None, int]:
    found = _gather(
        EveryOrg(),
        None,
        model,
        period.start,
        period.end,
        ("org_id",),
        OrgUsageHour,
        lambda rows: rows.values("org_id").annotate(
            tokens=Sum(F("input_tokens") + F("output_tokens"))
        ),
        lambda rows: rows.values(org_id=key_org("token__")).annotate(tokens=Sum(TOKENS)),
        sealed,
    )
    return {org_id: counts["tokens"] for (org_id,), counts in found.items()}


def org_heat(
    extent: OrgWide,
    start: datetime,
    end: datetime,
    sealed: "Sealed | None" = None,
    requests: QuerySet[Request] | None = None,
    model: str | None = None,
) -> dict[tuple[int, int], int]:
    found = _gather(
        extent,
        requests,
        model,
        start,
        end,
        ("day", "at"),
        OrgUsageHour,
        lambda rows: rows.values(day=ExtractIsoWeekDay("hour"), at=ExtractHour("hour")).annotate(
            tokens=Sum(F("input_tokens") + F("output_tokens"))
        ),
        lambda rows: rows.values(
            day=ExtractIsoWeekDay("timestamp"), at=ExtractHour("timestamp")
        ).annotate(tokens=Sum(TOKENS)),
        sealed,
    )
    return {key: counts["tokens"] for key, counts in found.items()}


def key_window_totals(extent: OrgWide, model: str | None, period: Period) -> dict[int, Activity]:
    requests = served(requests_of(extent))
    kept = KeyUsageWindow.objects.filter(
        model__in=model_names(),
        token__in=Token.objects.filter(extent.keys()),
        window_start=period.start,
        sealed_until__lte=period.end,
    )
    if model is not None:
        requests, kept = requests.filter(model=model), kept.filter(model=model)
    rows: list[Row] = list(
        kept.values("token_id", "sealed_until")
        .annotate(
            requests=Sum("requests"),
            failed=Sum("failed"),
            input=Sum("input_tokens"),
            cached=Sum("cached_input_tokens"),
            output=Sum("output_tokens"),
        )
        .order_by()
    )
    raw_start = period.start
    for row in rows:
        raw_start = row.pop("sealed_until")
    rows += (
        requests.filter(timestamp__gte=raw_start, timestamp__lt=period.end)
        .values("token_id")
        .annotate(**TOTALS)
        .order_by()
    )
    return {
        token_id: _activity(counts) for (token_id,), counts in _merged(rows, ("token_id",)).items()
    }


def approximate_median(counts: Mapping[int, int]) -> int | None:
    total = sum(counts.values())
    if not total:
        return None
    ordered = sorted(counts.items())
    seen = accumulate(n for _, n in ordered)
    bucket = next(
        b for (b, _), up_to in zip(ordered, seen, strict=True) if up_to > (total - 1) // 2
    )
    if bucket == ZERO_BUCKET:
        return 0
    return round(2 ** ((bucket + 0.5) / BUCKETS_PER_DOUBLING))


@dataclass(frozen=True)
class Sealed:
    until: datetime | None

    @classmethod
    def read(cls) -> "Sealed":
        if not steady_hours(settings.TIME_ZONE):
            return cls(None)
        return cls(
            UsageRollupState.objects.filter(name=HOURLY, time_zone=settings.TIME_ZONE)
            .values_list("sealed_until", flat=True)
            .first()
        )

    def current(self, now: datetime) -> bool:
        return self.until is not None and self.until >= now - CURRENT


def _split(
    start: datetime, end: datetime, sealed_until: datetime | None
) -> tuple[Q | None, Q | None]:
    if sealed_until is not None:
        first, last = _ceil_hour(start), min(sealed_until, floor_hour(end))
        if first < last:
            rest = [
                Q(timestamp__gte=low, timestamp__lt=high)
                for low, high in ((start, first), (last, end))
                if low < high
            ]
            return Q(hour__gte=first, hour__lt=last), reduce(operator.or_, rest) if rest else None
    return None, Q(timestamp__gte=start, timestamp__lt=end)


def _gather(
    extent: OrgWide,
    requests: QuerySet[Request] | None,
    model: str | None,
    start: datetime,
    end: datetime,
    keys: tuple[str, ...],
    table: type[OrgUsageHour] | type[OrgLatencyHour],
    from_rollup: Callable[[QuerySet[Any]], Iterable[Row]],
    from_requests: Callable[[QuerySet[Request]], Iterable[Row]],
    sealed: Sealed | None,
) -> Grouped:
    hours, rest = _split(start, end, (sealed or Sealed.read()).until)
    if requests is None:
        requests = served(requests_of(extent))
    if model is not None:
        requests = requests.filter(model=model)
    rows = [] if rest is None else list(from_requests(requests.filter(rest).order_by()))
    if hours is not None:
        orgs = Q(org=None) | Q(org__in=Org.objects.all())
        kept = table.objects.filter(
            hours,
            Q(org=extent.org) if isinstance(extent, WholeOrg) else orgs,
            model__in=model_names(),
        )
        if model is not None:
            kept = kept.filter(model=model)
        rows += from_rollup(kept.order_by())
    return _merged(rows, keys)


def _merged(rows: Iterable[Row], keys: tuple[str, ...]) -> Grouped:
    found: Grouped = defaultdict(Counter)
    for row in rows:
        found[tuple(row.pop(key) for key in keys)].update(row)
    return dict(found)


def _activity(counts: Counter[str]) -> Activity:
    return Activity(
        requests=counts["requests"],
        failed=counts["failed"],
        input=counts["input"],
        cached=counts["cached"],
        output=counts["output"],
    )
