"""Usage figures of one extent over one period.

`UsageSource.of` decides once whether the rollup tables answer: they hold whole org-wide
hours, so they serve a whole org or every org over hourly or coarser buckets. Every reader
returns the same figures from either source; only model medians become approximate.
"""

import operator
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import reduce
from itertools import accumulate
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db.models import Count, Expression, F, Q, QuerySet, Subquery, Sum, Value
from django.db.models.functions import Coalesce, ExtractHour, ExtractIsoWeekDay, Trunc

from gateway.config import model_names
from management.access import OrgWide, WholeOrg, requests_of
from management.activity import TOKENS, TOTALS, Activity, ModelUsage, Series, model_usage, served
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
from management.outcomes import OUTCOME_COUNTS, Outcome
from management.ownership import key_org
from management.periods import HOUR, Period
from management.usage_rollup import (
    BUCKET,
    BUCKETS_PER_DOUBLING,
    KEY_WINDOWS,
    ZERO_BUCKET,
    floor_hour,
    steady_hours,
)

if TYPE_CHECKING:
    from management.request_filters import Filters

CURRENT = timedelta(hours=2)

_FAILED_COLUMNS = reduce(operator.add, (F(o.column) for o in Outcome if o.failed))
_HOUR_TOTALS = {
    "requests": Sum(F(Outcome.OK.column) + _FAILED_COLUMNS + F("unfinished"), default=0),
    "failed": Sum(_FAILED_COLUMNS, default=0),
    "input": Sum("input_tokens", default=0),
    "cached": Sum("cached_input_tokens", default=0),
    "output": Sum("output_tokens", default=0),
}
_HOUR_OUTCOME_COUNTS = {o.name: Sum(o.column, default=0) for o in Outcome}
_HOUR_TOKENS = Sum(F("input_tokens") + F("output_tokens"))

Row = dict[str, Any]
Grouped = dict[tuple[Any, ...], Counter[str]]


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


@dataclass(frozen=True)
class Rollup:
    """The rollup rows that sum a source's requests: those of `extent` and `model`."""

    extent: OrgWide
    model: str | None
    sealed: Sealed | None = None
    key_windows: bool = False


@dataclass(frozen=True)
class UsageSource:
    requests: QuerySet[Request]
    period: Period
    rollup: Rollup | None = None

    @classmethod
    def of(
        cls, filters: "Filters", sealed: Sealed | None = None, narrower: Q | None = None
    ) -> "UsageSource":
        """A `narrower` selection reads the requests: no rollup row can tell it apart."""
        extent, period = filters.org_wide, filters.period
        if narrower is not None or extent is None or period.step.size < HOUR.size:
            requests = filters.requests if narrower is None else filters.requests.filter(narrower)
            return cls(requests, period)
        rollup = Rollup(
            extent, filters.model, sealed, key_windows=filters.span in KEY_WINDOWS.values()
        )
        return cls(filters.requests, period, rollup)

    @classmethod
    def whole(
        cls,
        extent: OrgWide,
        model: str | None,
        period: Period,
        sealed: Sealed | None = None,
        *,
        key_windows: bool = False,
    ) -> "UsageSource":
        requests = served(requests_of(extent))
        if model is not None:
            requests = requests.filter(model=model)
        rollup = Rollup(extent, model, sealed, key_windows)
        return cls(requests, period, rollup)

    @property
    def approximate(self) -> bool:
        return self.rollup is not None

    def series(self) -> Series:
        kind = self.period.step.kind
        found = self._gather(
            ("bucket",),
            OrgUsageHour,
            lambda rows: rows.values(bucket=Trunc("hour", kind)).annotate(
                **_HOUR_TOTALS, **_HOUR_OUTCOME_COUNTS
            ),
            lambda rows: rows.values(bucket=Trunc("timestamp", kind)).annotate(
                **TOTALS, **OUTCOME_COUNTS
            ),
        )
        return Series(
            [
                (bucket, _activity(found.get((bucket,), Counter())))
                for bucket in self.period.buckets
            ],
            {o: sum(c[o.name] for c in found.values()) for o in Outcome},
        )

    def models(self) -> dict[str, ModelUsage]:
        if self.rollup is None:
            return model_usage(self.period.of(self.requests))
        totals = self._gather(
            ("model",),
            OrgUsageHour,
            lambda rows: rows.values("model").annotate(**_HOUR_TOTALS),
            lambda rows: rows.values("model").annotate(**TOTALS),
        )
        latency = self._gather(
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

    def org_tokens(self) -> dict[int | None, int]:
        found = self._gather(
            ("org_id",),
            OrgUsageHour,
            lambda rows: rows.values("org_id").annotate(tokens=_HOUR_TOKENS),
            lambda rows: rows.values(org_id=key_org("token__")).annotate(tokens=Sum(TOKENS)),
        )
        return {org_id: counts["tokens"] for (org_id,), counts in found.items()}

    def heat(self) -> dict[tuple[int, int], int]:
        found = self._gather(
            ("day", "at"),
            OrgUsageHour,
            lambda rows: rows.values(
                day=ExtractIsoWeekDay("hour"), at=ExtractHour("hour")
            ).annotate(tokens=_HOUR_TOKENS),
            lambda rows: rows.values(
                day=ExtractIsoWeekDay("timestamp"), at=ExtractHour("timestamp")
            ).annotate(tokens=Sum(TOKENS)),
        )
        return {key: counts["tokens"] for key, counts in found.items()}

    def key_totals(self) -> dict[int, Activity]:
        rows: list[Row] = []
        start: datetime | Expression = self.period.start
        if self.rollup is not None and self.rollup.key_windows:
            rows, start = self._key_windows(self.rollup)
        rows += (
            self.requests.filter(timestamp__gte=start, timestamp__lt=self.period.end)
            .values("token_id")
            .annotate(**TOTALS)
            .order_by()
        )
        return {
            token_id: _activity(counts)
            for (token_id,), counts in _merged(rows, ("token_id",)).items()
        }

    def _key_windows(self, rollup: Rollup) -> tuple[list[Row], datetime | Expression]:
        """The window's rows of the keys, and where the requests after them start: a key with
        no row in the window has no requests before its seal either."""
        period = self.period
        window = KeyUsageWindow.objects.filter(
            window_start=period.start, sealed_until__lte=period.end
        )
        kept = window.filter(
            model__in=model_names(), token__in=Token.objects.filter(rollup.extent.keys())
        )
        if rollup.model is not None:
            kept = kept.filter(model=rollup.model)
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
        seals = {row.pop("sealed_until") for row in rows}
        if seals:
            return rows, min(seals)
        sealed = Subquery(window.order_by().values("sealed_until")[:1])
        return rows, Coalesce(sealed, Value(period.start))

    def _gather(
        self,
        keys: tuple[str, ...],
        table: type[OrgUsageHour] | type[OrgLatencyHour],
        from_rollup: Callable[[QuerySet[Any]], Iterable[Row]],
        from_requests: Callable[[QuerySet[Request]], Iterable[Row]],
    ) -> Grouped:
        rollup = self.rollup
        sealed_until = None if rollup is None else (rollup.sealed or Sealed.read()).until
        hours, rest = _split(self.period.start, self.period.end, sealed_until)
        rows = [] if rest is None else list(from_requests(self.requests.filter(rest).order_by()))
        if hours is not None and rollup is not None:
            orgs = Q(org=None) | Q(org__in=Org.objects.all())
            kept = table.objects.filter(
                hours,
                Q(org=rollup.extent.org) if isinstance(rollup.extent, WholeOrg) else orgs,
                model__in=model_names(),
            )
            if rollup.model is not None:
                kept = kept.filter(model=rollup.model)
            rows += from_rollup(kept.order_by())
        return _merged(rows, keys)


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


def _ceil_hour(moment: datetime) -> datetime:
    floor = floor_hour(moment)
    return floor if floor == moment else floor + HOUR.size


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
