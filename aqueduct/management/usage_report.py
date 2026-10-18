from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from django.db.models import Count, Max, OuterRef, QuerySet, Subquery, Sum
from django.db.models.functions import Lower
from django.utils.functional import Promise
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from management.activity import TOKENS, TOTALS, Activity, ModelUsage, Series
from management.charts import Share, scaled
from management.formatting import MISSING, NO_ORG, compact, duration, percent
from management.models import Org, Request, Token
from management.outcomes import STATUSES
from management.ownership import RELATED, key_org

if TYPE_CHECKING:
    from management.request_log import Key

TOP_KEYS = 10
TOP_USER_IDS = 25
APPROXIMATE_MEDIAN = _("Approximate")


@dataclass(frozen=True)
class StatusRow:
    label: str | Promise
    codes: str
    icon: str
    tone: str
    count: int
    share: str
    href: str


def statuses(timeline: Series, link: Callable[..., str]) -> tuple[StatusRow, ...]:
    total = timeline.total.requests
    return tuple(
        StatusRow(label, codes, icon, tone, count, percent(count, total), link(status=codes))
        for (label, codes, icon, tone, _), count in zip(
            STATUSES, timeline.status_counts, strict=True
        )
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


def _figures(
    activities: Sequence[Activity], total: int, busiest: int | None = None
) -> list[tuple[Activity, str, int]]:
    return [
        (activity, percent(activity.tokens, total) if activity.tokens else MISSING, width)
        for activity, width in zip(
            activities, scaled((a.tokens for a in activities), busiest), strict=True
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
        for (name, count), width in zip(rows, scaled(count for _, count in rows), strict=True)
    )
