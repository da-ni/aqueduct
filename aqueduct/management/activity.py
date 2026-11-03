from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Count, QuerySet, Sum

from gateway.config import model_names
from management.models import Request
from management.outcomes import FAILED, Outcome

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


def summed(activities: Sequence[Activity]) -> Activity:
    return Activity(**{field: sum(getattr(a, field) for a in activities) for field in TOTALS})


def served(requests: QuerySet[Request]) -> QuerySet[Request]:
    return requests.filter(model__in=model_names())


@dataclass(frozen=True)
class Series:
    points: list[tuple[datetime, Activity]]
    outcomes: dict[Outcome, int]

    @property
    def total(self) -> Activity:
        return summed([a for _, a in self.points])
