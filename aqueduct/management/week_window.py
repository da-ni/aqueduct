from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import QuerySet
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.translation import gettext

from management.activity import TOTALS, Activity, served, summed
from management.charts import Stat, spark
from management.formatting import (
    as_cached,
    as_failed,
    as_requests,
    as_tokens,
    compact,
    day_label,
    grouped,
)
from management.models import Request
from management.periods import week


@dataclass(frozen=True)
class Day:
    day: date
    activity: Activity


@dataclass(frozen=True)
class Window:
    totals: Activity
    days: tuple[Day, ...]

    @property
    def stats(self) -> tuple[Stat, ...]:
        totals = self.totals

        def daily(field: str) -> list[tuple[str, float]]:
            return [(day_label(d.day), getattr(d.activity, field)) for d in self.days]

        return (
            Stat(gettext("Input tokens"), compact(totals.input), spark(daily("input"), as_tokens)),
            Stat(
                gettext("Output tokens"), compact(totals.output), spark(daily("output"), as_tokens)
            ),
            Stat(gettext("Cache hits"), totals.cache_hits, spark(daily("cached"), as_cached)),
            Stat(
                gettext("Requests"), grouped(totals.requests), spark(daily("requests"), as_requests)
            ),
            Stat(gettext("Failed"), totals.failed_share, spark(daily("failed"), as_failed)),
        )


def window(requests: QuerySet[Request], now: datetime) -> Window:
    period = week(now)
    per_day = {
        row.pop("day"): Activity(**row)
        for row in served(period.of(requests))
        .annotate(day=TruncDate("timestamp"))
        .values("day")
        .annotate(**TOTALS)
        .order_by()
    }
    days = tuple(
        Day(day, per_day.get(day, Activity()))
        for day in (timezone.localdate(bucket) for bucket in period.buckets)
    )
    return Window(summed(list(per_day.values())), days)
