import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import Case, Count, Expression, F, IntegerField, Q, QuerySet, Sum, Value, When
from django.db.models.functions import Cast, Floor, Ln, Trunc
from django.utils import timezone

from management.models import (
    KeyUsageWindow,
    OrgLatencyHour,
    OrgUsageHour,
    Request,
    UsageRollupState,
)
from management.models.usage_rollup import HOURLY
from management.outcomes import FAILED, Outcome
from management.ownership import key_org
from management.periods import HOUR, Period, Span

SETTLE = timedelta(minutes=15)
REWRITE = timedelta(hours=24)
KEY_WINDOWS: Mapping[int, Span] = {7: Span.WEEK, 30: Span.MONTH}

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


Row = dict[str, Any]


def floor_hour(moment: datetime) -> datetime:
    """In UTC: adding a timedelta to a zoned datetime is wall-clock arithmetic."""
    return HOUR.floor(moment).astimezone(UTC)


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
