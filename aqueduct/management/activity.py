from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db.models import Aggregate, Count, F, IntegerField, QuerySet, Sum
from django.db.models.functions import Trunc

from gateway.config import model_names
from management.formatting import compact, percent
from management.models import Request
from management.outcomes import FAILED, OUTCOME_COUNTS, Outcome
from management.periods import Period

TOKENS = F("input_tokens") + F("output_tokens")
TOTALS = {
    "requests": Count("id"),
    "failed": Count("id", filter=FAILED),
    "input": Sum("input_tokens", default=0),
    "cached": Sum("cached_input_tokens", default=0),
    "output": Sum("output_tokens", default=0),
}


@dataclass(frozen=True)
class Activity:
    requests: int = 0
    failed: int = 0
    input: int = 0
    cached: int = 0
    output: int = 0
    last_used: datetime | None = None

    @property
    def tokens(self) -> int:
        return self.input + self.output

    @property
    def tokens_short(self) -> str:
        return compact(self.tokens)

    @property
    def cache_hits(self) -> str:
        return percent(self.cached, self.input)

    @property
    def failed_share(self) -> str:
        return percent(self.failed, self.requests)

    @property
    def segments(self) -> tuple[tuple[str, int], ...]:
        return (("c", self.cached), ("n", self.input - self.cached), ("o", self.output))


def summed(activities: Sequence[Activity]) -> Activity:
    return Activity(**{field: sum(getattr(a, field) for a in activities) for field in TOTALS})


def served(requests: QuerySet[Request]) -> QuerySet[Request]:
    return requests.filter(model__in=model_names())


def activity_by(field: str, ids: Iterable[int], period: Period) -> dict[int, Activity]:
    rows = (
        served(period.of(Request.objects.filter(**{f"{field}__in": list(ids)})))
        .values(field)
        .annotate(**TOTALS)
        .order_by()
    )
    return {row.pop(field): Activity(**row) for row in rows}


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


@dataclass(frozen=True)
class Series:
    points: list[tuple[datetime, Activity]]
    outcomes: dict[Outcome, int]

    @property
    def total(self) -> Activity:
        return summed([a for _, a in self.points])


def series(requests: QuerySet[Request], period: Period) -> Series:
    rows = (
        period.of(requests)
        .annotate(bucket=Trunc("timestamp", period.step.kind))
        .values("bucket")
        .annotate(**TOTALS, **OUTCOME_COUNTS)
        .order_by()
    )
    found, outcomes = {}, dict.fromkeys(Outcome, 0)
    for row in rows:
        bucket = row.pop("bucket")
        for outcome in Outcome:
            outcomes[outcome] += row.pop(outcome.name)
        found[bucket] = Activity(**row)
    return Series([(bucket, found.get(bucket, Activity())) for bucket in period.buckets], outcomes)


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
