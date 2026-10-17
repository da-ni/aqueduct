"""Rollups of the request log, so that org-wide figures read thousands of rows, not millions.

`refresh()` keeps per-hour totals of each org and model (`OrgUsageHour`, `OrgLatencyHour`) up
to the last sealed hour. `refresh_key_windows()` keeps the 7- and 30-day totals of each key
(`KeyUsageWindow`). The read helpers combine those rows with the raw requests they do not cover,
so they return what the raw queries in `usage_report` return, except that medians are
approximate. Without rollups they read the raw requests only."""

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from functools import cache
from itertools import accumulate
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import Case, Count, Expression, F, IntegerField, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Cast, ExtractHour, ExtractIsoWeekDay, Floor, Ln, Trunc
from django.utils import timezone

from management.key_activity import FAILED, Activity, router_models, served
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
from management.usage_report import (
    HOUR,
    ORG,
    STATUS_COUNTS,
    STATUSES,
    TOKENS,
    TOTALS,
    ModelUsage,
    Period,
    Series,
    Span,
    org_requests,
)

SETTLE = timedelta(minutes=15)
"""How long a request may still change before its hour is sealed."""
REWRITE = timedelta(hours=24)
"""How far back each refresh recounts sealed hours, to pick up late status and token updates."""
KEY_WINDOWS: Mapping[int, Span] = {7: Span.WEEK, 30: Span.MONTH}

BUCKETS_PER_DOUBLING = 8
ZERO_BUCKET = -1
BUCKET = Case(
    When(response_time_ms=0, then=Value(ZERO_BUCKET)),
    default=Cast(
        Floor(Ln("response_time_ms") * Value(BUCKETS_PER_DOUBLING / math.log(2))), IntegerField()
    ),
)
"""Bucket `b` holds response times in [2^(b/8), 2^((b+1)/8)) ms, and `ZERO_BUCKET` those of 0 ms."""

STATUS_COLUMNS = ("ok", "rate_limited", "client_error", "server_error")
_COUNT_STATUSES = {
    column: Count("id", filter=match)
    for column, (*_, match) in zip(STATUS_COLUMNS, STATUSES, strict=True)
} | {"unfinished": Count("id", filter=Q(status_code__isnull=True))}
_SUM_TOKENS = {
    field: Sum(field, default=0)
    for field in ("input_tokens", "cached_input_tokens", "output_tokens")
}

_FAILED_COLUMNS = F("rate_limited") + F("client_error") + F("server_error")
_HOUR_TOTALS = {
    "requests": Sum(F("ok") + _FAILED_COLUMNS + F("unfinished"), default=0),
    "failed": Sum(_FAILED_COLUMNS, default=0),
    "input": Sum("input_tokens", default=0),
    "cached": Sum("cached_input_tokens", default=0),
    "output": Sum("output_tokens", default=0),
}
"""`usage_report.TOTALS` over `OrgUsageHour` rows."""
_HOUR_STATUS_COUNTS = {f"s{i}": Sum(column, default=0) for i, column in enumerate(STATUS_COLUMNS)}
"""`usage_report.STATUS_COUNTS` over `OrgUsageHour` rows."""


Row = dict[str, Any]
Grouped = dict[tuple[Any, ...], Counter[str]]
"""Summed counts per group key."""


def floor_hour(moment: datetime) -> datetime:
    """The start of the local hour of `moment`, in UTC so that adding hours stays exact."""
    return HOUR.floor(moment).astimezone(UTC)


def _ceil_hour(moment: datetime) -> datetime:
    floor = floor_hour(moment)
    return floor if floor == moment else floor + HOUR.size


@cache
def steady_hours(zone: str) -> bool:
    """Whether local hours in `zone` start the same minutes past UTC hours all year, as
    `_local_hour` needs. Not so in Lord Howe, whose clocks move by 30 minutes: there the org
    rollups stay empty and the read helpers count raw requests."""
    tz = ZoneInfo(zone)
    year = timezone.now().year
    shifts = {
        (datetime(year, month, 1, tzinfo=tz).utcoffset() or timedelta()) % HOUR.size
        for month in range(1, 13)
    }
    return len(shifts) == 1


def _local_hour() -> Expression:
    """`Trunc("timestamp", "hour")` in the local time zone, as an instant the database can insert
    as it is. Local hours start a fixed `shift` past UTC hours where `steady_hours` holds."""
    shift = (timezone.localtime().utcoffset() or timedelta()) % HOUR.size
    if not shift:
        return Trunc("timestamp", "hour", tzinfo=UTC)
    return Trunc(F("timestamp") - shift, "hour", tzinfo=UTC) + shift


def key_window_name(days: int) -> str:
    return f"keys-{days}d"


# Writing ----------------------------------------------------------------------------------------


def refresh(now: datetime | None = None, *, full: bool = False) -> None:
    """Recount the hours since 24 hours before the last sealed hour, or every hour when `full`,
    when the rollups are missing or marked dirty, or when they were bucketed in another time
    zone. Seals the hours that ended 15 minutes before `now`."""
    if not steady_hours(settings.TIME_ZONE):
        return
    sealed = floor_hour((now or timezone.now()) - SETTLE)
    with transaction.atomic():
        state = _locked(HOURLY)
        stale = full or state.dirty or state.time_zone != settings.TIME_ZONE
        start = None if stale or state.sealed_until is None else state.sealed_until
        if start is not None:
            start = min(start, sealed - REWRITE)
        hours = Q() if start is None else Q(hour__gte=start)
        OrgUsageHour.objects.filter(hours).delete()
        OrgLatencyHour.objects.filter(hours).delete()

        recounted = Request.objects.filter(timestamp__lt=sealed).order_by()
        if start is not None:
            recounted = recounted.filter(timestamp__gte=start)
        by_hour = {"hour": _local_hour(), "org_id": ORG}
        _insert(
            OrgUsageHour,
            recounted.values("model", **by_hour).annotate(**_COUNT_STATUSES, **_SUM_TOKENS),
        )
        _insert(
            OrgLatencyHour,
            recounted.filter(response_time_ms__isnull=False)
            .values("model", **by_hour, bucket=BUCKET)
            .annotate(requests=Count("id")),
        )
        state.sealed_until, state.dirty, state.time_zone = sealed, False, settings.TIME_ZONE
        state.save()


def rebuild(now: datetime | None = None) -> None:
    refresh(now, full=True)


def refresh_key_windows(now: datetime | None = None) -> None:
    """Recount each key's last 7 and 30 days, as `Period.last` bounds them, up to the last
    sealed hour."""
    now = now or timezone.now()
    end = floor_hour(now - SETTLE)
    for days, span in KEY_WINDOWS.items():
        start = Period.last(span, now).start
        with transaction.atomic():
            state = _locked(key_window_name(days))
            KeyUsageWindow.objects.filter(days=days).delete()
            _insert(
                KeyUsageWindow,
                Request.objects.filter(timestamp__gte=start, timestamp__lt=end)
                .values("token_id", "model")
                .annotate(requests=Count("id"), failed=Count("id", filter=FAILED), **_SUM_TOKENS)
                .order_by(),
                days=days,
            )
            state.window_start, state.sealed_until = start, end
            state.dirty, state.time_zone = False, settings.TIME_ZONE
            state.save()


def _locked(name: str) -> UsageRollupState:
    UsageRollupState.objects.get_or_create(
        name=name, defaults={"dirty": True, "time_zone": settings.TIME_ZONE}
    )
    return UsageRollupState.objects.select_for_update().get(name=name)


def _insert(table: type[models.Model], rows: QuerySet[Any, Row], **fixed: int) -> None:
    """Insert the grouped `rows`, plus the `fixed` values, without fetching them: an ORM insert
    of a full rebuild's few hundred thousand rows takes several times as long as the grouping."""
    names = [*fixed, *rows.query.values_select, *rows.query.annotation_select]
    quote = connection.ops.quote_name
    column = {field.attname: field.column for field in table._meta.concrete_fields}
    columns = ", ".join(quote(column[name]) for name in names)
    picked = ", ".join(["%s"] * len(fixed) + [quote(name) for name in names[len(fixed) :]])
    select, params = rows.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {quote(table._meta.db_table)} ({columns}) "  # noqa: S608
            f"SELECT {picked} FROM ({select}) grouped",
            [*fixed.values(), *params],
        )


# Reading ----------------------------------------------------------------------------------------


def org_series(org: Org | None, model: str | None, period: Period) -> Series:
    """`usage_report.series` of the requests of `org`, every org when `None`, and of `model`,
    every served model when `None`."""
    found = _gather(
        org,
        model,
        period.start,
        period.end,
        ("bucket",),
        OrgUsageHour,
        lambda rows: rows.values(bucket=Trunc("hour", period.step.kind)).annotate(
            **_HOUR_TOTALS, **_HOUR_STATUS_COUNTS
        ),
        lambda rows: rows.values(bucket=Trunc("timestamp", period.step.kind)).annotate(
            **TOTALS, **STATUS_COUNTS
        ),
        hourly=period.step.size >= HOUR.size,
    )
    return Series(
        [(bucket, _activity(found.get((bucket,), Counter()))) for bucket in period.buckets],
        tuple(sum(c[f"s{i}"] for c in found.values()) for i in range(len(STATUSES))),
    )


def org_models(org: Org | None, model: str | None, period: Period) -> dict[str, ModelUsage]:
    """Activity and approximate median response time per model, for the models with requests.
    Each median is within 4.5% of the lower median response time, or 1 ms below 12 ms."""
    args = (org, model, period.start, period.end)
    totals = _gather(
        *args,
        ("model",),
        OrgUsageHour,
        lambda rows: rows.values("model").annotate(**_HOUR_TOTALS),
        lambda rows: rows.values("model").annotate(**TOTALS),
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
    )
    buckets: dict[str, dict[int, int]] = defaultdict(dict)
    for (name, bucket), counts in latency.items():
        buckets[name][bucket] = counts["n"]
    return {
        name: ModelUsage(_activity(counts), approximate_median(buckets[name]))
        for (name,), counts in totals.items()
    }


def org_tokens(model: str | None, period: Period) -> dict[int | None, int]:
    """Tokens per org id, `None` for keys of no org, as `usage_report.org_tokens` sums them."""
    found = _gather(
        None,
        model,
        period.start,
        period.end,
        ("org_id",),
        OrgUsageHour,
        lambda rows: rows.values("org_id").annotate(
            tokens=Sum(F("input_tokens") + F("output_tokens"))
        ),
        lambda rows: rows.values(org_id=ORG).annotate(tokens=Sum(TOKENS)),
    )
    return {org_id: counts["tokens"] for (org_id,), counts in found.items()}


def org_heat(org: Org | None, start: datetime, end: datetime) -> dict[tuple[int, int], int]:
    """Tokens per ISO weekday and local hour of day, as `usage_report.heat` sums them."""
    found = _gather(
        org,
        None,
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
    )
    return {key: counts["tokens"] for key, counts in found.items()}


def key_window_totals(
    tokens: QuerySet[Token] | None, model: str | None, period: Period
) -> dict[int, Activity]:
    """Activity per key id in `period`, for the keys of `tokens`, every key when `None`, with
    requests in the period. Reads a key window when one starts where `period` does."""
    requests = served(Request.objects.all())
    kept = KeyUsageWindow.objects.filter(model__in=router_models())
    if tokens is not None:
        requests, kept = requests.filter(token__in=tokens), kept.filter(token__in=tokens)
    if model is not None:
        requests, kept = requests.filter(model=model), kept.filter(model=model)
    windows = UsageRollupState.objects.filter(
        name__in=[key_window_name(days) for days in KEY_WINDOWS],
        window_start=period.start,
        sealed_until__lte=period.end,
    )
    rows: list[Row] = []
    raw_start = period.start
    for _ in range(2):  # Use the rows only if no refresh replaced them while they were read.
        window = windows.first()
        if window is None or window.sealed_until is None:
            break
        days = next(d for d in KEY_WINDOWS if key_window_name(d) == window.name)
        found = list(
            kept.filter(days=days)
            .values("token_id")
            .annotate(
                requests=Sum("requests"),
                failed=Sum("failed"),
                input=Sum("input_tokens"),
                cached=Sum("cached_input_tokens"),
                output=Sum("output_tokens"),
            )
            .order_by()
        )
        if windows.filter(name=window.name, sealed_until=window.sealed_until).exists():
            rows, raw_start = found, window.sealed_until
            break
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
    """The lower median of response times counted per `BUCKET`, as its bucket's geometric
    midpoint."""
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


def _sealed_until() -> datetime | None:
    if not steady_hours(settings.TIME_ZONE):
        return None
    return (
        UsageRollupState.objects.filter(name=HOURLY, time_zone=settings.TIME_ZONE)
        .values_list("sealed_until", flat=True)
        .first()
    )


def _split(start: datetime, end: datetime, sealed_until: datetime | None) -> tuple[Q | None, Q]:
    """The hours of [start, end) that the rollups hold, and the requests of the rest."""
    if sealed_until is not None:
        first, last = _ceil_hour(start), min(sealed_until, floor_hour(end))
        if first < last:
            return Q(hour__gte=first, hour__lt=last), Q(
                timestamp__gte=start, timestamp__lt=first
            ) | Q(timestamp__gte=last, timestamp__lt=end)
    return None, Q(timestamp__gte=start, timestamp__lt=end)


def _gather(
    org: Org | None,
    model: str | None,
    start: datetime,
    end: datetime,
    keys: tuple[str, ...],
    table: type[OrgUsageHour] | type[OrgLatencyHour],
    from_rollup: Callable[[QuerySet[Any]], Iterable[Row]],
    from_requests: Callable[[QuerySet[Request]], Iterable[Row]],
    *,
    hourly: bool = True,
) -> Grouped:
    """Counts per `keys` over [start, end), from rollup rows where they exist and from requests
    elsewhere."""
    hours, rest = _split(start, end, _sealed_until() if hourly else None)
    requests = org_requests(org).filter(rest)
    if model is not None:
        requests = requests.filter(model=model)
    rows = list(from_requests(requests.order_by()))
    if hours is not None:
        kept = table.objects.filter(hours, model__in=router_models())
        if org is not None:
            kept = kept.filter(org=org)
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
