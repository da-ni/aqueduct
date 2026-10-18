import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from django.core.cache import cache
from django.db.models import Count, F, Q, QuerySet
from django.db.models.expressions import CombinedExpression, OrderBy
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext

from gateway.config import model_names
from management.access import EveryOrg, KeyAct, OrgWide, TeamAct, Viewer, requests_of
from management.activity import served
from management.formatting import NO_ORG, duration, grouped
from management.ids import MAX_ID, parse_id
from management.model_catalog import scopes_for, usable_models
from management.models import Request, Token
from management.outcomes import FAILED, OUTCOME_COUNTS, Outcome
from management.ownership import RELATED, owner
from management.periods import Period, Span
from management.usage_rollup import org_series

PAGE_SIZE = 50
_MAX_PAGE = MAX_ID // PAGE_SIZE


FAILED_TAB = "failed"
_STATUS_FILTERS = {FAILED_TAB: FAILED} | {o.codes: o.matches for o in Outcome}


@dataclass(frozen=True)
class Key:
    token: Token
    href: str | None
    note: str
    own: bool = False

    @property
    def name(self) -> str:
        return self.token.name


def describe_key(token: Token, viewer: Viewer, *, with_org: bool = False) -> Key:
    href = reverse("token_detail", args=[token.id]) if viewer.may_key(KeyAct.OPEN, token) else None
    account, own = token.service_account, False
    if account is not None:
        note = gettext("Service account · %(team)s") % {"team": account.team.name}
    elif token.user_id == viewer.user_id:
        note, own = gettext("Personal"), True
    else:
        note = token.user.email
    if with_org:
        found = owner(token)
        note += f" · {found.org.name if found is not None else NO_ORG}"
    return Key(token, href, note, own)


@dataclass(frozen=True)
class Filters:
    viewer: Viewer
    mine: bool
    keys: tuple[Key, ...]
    only_key: Key | None
    key: Key | None
    key_q: str
    matches: tuple[Key, ...]
    page_params: tuple[tuple[str, str], ...]
    models: tuple[str, ...]
    model: str | None
    span: Span
    period: Period
    requests: QuerySet[Request]

    @property
    def can_pick_org(self) -> bool:
        return self.viewer.can_switch_org

    @property
    def can_pick_scope(self) -> bool:
        return self.viewer.sees_others

    @property
    def org_wide(self) -> OrgWide | None:
        return None if self.mine or self.key is not None else self.viewer.org_wide

    @property
    def visibility(self) -> tuple[object, ...]:
        return (
            self.viewer.shown.cache_key,
            ("mine", self.viewer.user_id, *(k.token.id for k in self.keys)) if self.mine else (),
            self.key.token.id if self.key else None,
            self.model,
        )

    @property
    def spans(self) -> tuple[Span, ...]:
        return tuple(Span)

    @property
    def query(self) -> dict[str, str]:
        found = {}
        if self.can_pick_org:
            found["org"] = self.viewer.chosen.param
        if self.mine:
            found["scope"] = "mine"
        if self.key:
            found["key"] = str(self.key.token.id)
        if self.model:
            found["model"] = self.model
        found["span"] = self.span.value
        return found

    @property
    def search_params(self) -> tuple[tuple[str, str], ...]:
        return tuple((dict(self.page_params) | self.query).items())

    def log_url(self, **narrower: str) -> str:
        return f"{reverse('requests')}?{urlencode(self.query | narrower)}"


KEY_MATCHES = 20
_NOT_PAGE_PARAMS = frozenset(
    {"org", "scope", "key", "model", "span", "key_q", "before", "after", "request", "page"}
)


def key_search(text: str, *, by_owner: bool) -> Q:
    found = Q(name__icontains=text)
    return found | Q(user__email__icontains=text) if by_owner else found


def read_filters(
    viewer: Viewer, params: Mapping[str, str], *, default_span: Span, now: datetime
) -> Filters:
    sees_others = viewer.sees_others
    requests = served(requests_of(viewer.shown))
    if sees_others:
        names = set(model_names())
    else:
        used = requests.order_by().values_list("model", flat=True).distinct()
        names = {*usable_models(scopes_for(viewer.profile, viewer.teams(TeamAct.MINE))), *used}
    models = tuple(sorted(names, key=lambda name: (name.casefold(), name)))
    tokens = viewer.keys(KeyAct.SEE).select_related(*RELATED).order_by("name", "id")

    def describe(token: Token) -> Key:
        return describe_key(token, viewer, with_org=isinstance(viewer.chosen, EveryOrg))

    keys = tuple(describe(token) for token in tokens & viewer.keys(KeyAct.MINE))
    first_two = list(tokens[:2])
    only_key = describe(first_two[0]) if len(first_two) == 1 else None
    mine = sees_others and params.get("scope") == "mine"
    if mine:
        requests = requests.filter(token_id__in=[key.token.id for key in keys])

    key = None
    if raw_key := params.get("key", ""):
        if (key_id := parse_id(raw_key)) is None:
            raise Http404
        key = describe(get_object_or_404(tokens, pk=key_id))
        requests = requests.filter(token=key.token)

    key_q = params.get("key_q", "").strip()
    matches: tuple[Key, ...] = ()
    if key_q:
        found = tokens.filter(key_search(key_q, by_owner=sees_others))
        matches = tuple(describe(token) for token in found[:KEY_MATCHES])

    model = params.get("model") if params.get("model") in models else None
    if model is not None:
        requests = requests.filter(model=model)

    span = Span.parse(params.get("span", "")) or default_span
    period = Period.last(span, now)

    return Filters(
        viewer=viewer,
        mine=mine,
        keys=keys,
        only_key=only_key,
        key=key,
        key_q=key_q,
        matches=matches,
        page_params=tuple(
            (name, value) for name, value in params.items() if name not in _NOT_PAGE_PARAMS
        ),
        models=models,
        model=model,
        span=span,
        period=period,
        requests=requests,
    )


@dataclass(frozen=True)
class Row:
    request: Request
    key: Key
    is_open: bool

    @property
    def outcome(self) -> Outcome | None:
        return Outcome.of(self.request.status_code)

    @property
    def error(self) -> str | None:
        outcome = self.outcome
        if outcome is None:
            return gettext("No status was recorded.")
        return None if outcome.meaning is None else str(outcome.meaning)

    @property
    def response_time(self) -> str:
        return duration(self.request.response_time_ms)

    @property
    def overhead(self) -> str | None:
        ms = self.request.processing_time_ms
        return None if ms is None else duration(ms)

    @property
    def output_speed(self) -> str | None:
        tokens, ms = self.request.output_tokens, self.request.response_time_ms
        if not tokens or not ms:
            return None
        return gettext("%(speed)s tok/s") % {"speed": grouped(round(tokens * 1000 / ms))}


@dataclass(frozen=True)
class Tab:
    value: str | None
    label: str
    count: int
    codes: str | None = None


class Column(StrEnum):
    TIME = "time"
    MODEL = "model"
    STATUS = "status"
    TOKENS = "tokens"
    RESPONSE = "response"

    @property
    def expression(self) -> F | CombinedExpression:
        if self is Column.TOKENS:
            return F("input_tokens") + F("output_tokens")
        field = {
            Column.TIME: "timestamp",
            Column.MODEL: "model",
            Column.STATUS: "status_code",
            Column.RESPONSE: "response_time_ms",
        }[self]
        return F(field)

    @property
    def starts_descending(self) -> bool:
        return self is not Column.MODEL


@dataclass(frozen=True)
class Sort:
    column: Column
    descending: bool

    @classmethod
    def parse(cls, raw: str) -> "Sort | None":
        try:
            return cls(Column(raw.removeprefix("-")), raw.startswith("-"))
        except ValueError:
            return None

    def __str__(self) -> str:
        return f"{'-' if self.descending else ''}{self.column.value}"

    @property
    def ordering(self) -> tuple[OrderBy, OrderBy]:
        nulls_last = True if self.column in (Column.STATUS, Column.RESPONSE) else None
        if self.descending:
            return self.column.expression.desc(nulls_last=nulls_last), F("id").desc()
        return self.column.expression.asc(nulls_last=nulls_last), F("id").asc()

    @property
    def param(self) -> str | None:
        return None if self == NEWEST else str(self)


NEWEST = Sort(Column.TIME, descending=True)
BOUNDED_SPAN = timedelta(days=7)


def bounded_scan(filters: Filters) -> bool:
    return (
        filters.org_wide is None
        or filters.model is not None
        or filters.period.end - filters.period.start <= BOUNDED_SPAN
    )


@dataclass(frozen=True)
class Header:
    sortable: bool
    choice: str | None
    aria_sort: str | None


def _headers(sort: Sort, free: bool) -> dict[str, Header]:
    def header(column: Column) -> Header:
        if column is sort.column:
            choice = Sort(column, not sort.descending)
            aria_sort: str | None = "descending" if sort.descending else "ascending"
        else:
            choice, aria_sort = Sort(column, column.starts_descending), None
        return Header(free or column is Column.TIME, choice.param, aria_sort)

    return {column.value: header(column) for column in Column}


@dataclass(frozen=True)
class Cursor:
    timestamp: datetime
    id: int
    rank: int | None = None

    @classmethod
    def parse(cls, raw: str | None) -> "Cursor | None":
        micros, _, rest = (raw or "").partition(",")
        raw_id, _, raw_rank = rest.partition(",")
        pk = parse_id(raw_id)
        if pk is None or parse_id(micros) is None:
            return None
        rank = parse_id(raw_rank)
        try:
            return cls(_EPOCH + timedelta(microseconds=int(micros)), pk, rank)
        except OverflowError:
            return None

    @classmethod
    def at(cls, request: Request, rank: int | None = None) -> "Cursor":
        return cls(request.timestamp, request.id, rank)

    def __str__(self) -> str:
        micros = (self.timestamp - _EPOCH) // timedelta(microseconds=1)
        return f"{micros},{self.id}" + ("" if self.rank is None else f",{self.rank}")

    @property
    def older(self) -> Q:
        return Q(timestamp__lte=self.timestamp) & (
            Q(timestamp__lt=self.timestamp) | Q(id__lt=self.id)
        )

    @property
    def newer(self) -> Q:
        return Q(timestamp__gte=self.timestamp) & (
            Q(timestamp__gt=self.timestamp) | Q(id__gt=self.id)
        )

    def just_before(self, newest_first: bool) -> "Cursor":
        rank = None if self.rank is None else self.rank - 1
        return Cursor(self.timestamp, self.id + (1 if newest_first else -1), rank)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LAST = "last"


@dataclass(frozen=True)
class Place:
    before: str | None = None
    after: str | None = None
    page: str | None = None


@dataclass(frozen=True)
class Pager:
    here: Place
    first: Place | None
    previous: Place | None
    next: Place | None
    last: Place | None
    start: int | None

    @property
    def links(self) -> tuple[tuple[str, Place | None], ...]:
        return (
            (gettext("First"), self.first),
            (gettext("Previous"), self.previous),
            (gettext("Next"), self.next),
            (gettext("Last"), self.last),
        )


@dataclass(frozen=True)
class RequestLog:
    rows: tuple[Row, ...]
    tabs: tuple[Tab, ...]
    count: int
    status: str | None
    search: str
    searches_text: bool
    sort: Sort
    headers: Mapping[str, Header]
    pager: Pager
    shows_key: bool
    shows_overhead: bool

    @property
    def text_blocked(self) -> bool:
        return bool(self.search) and not self.searches_text and parse_id(self.search) is None

    @property
    def end(self) -> int | None:
        start = self.pager.start
        return None if start is None else start + len(self.rows) - 1


SEARCH_MAX = 256


def request_search(text: str, *, by_text: bool) -> Q:
    pk = parse_id(text)
    found = Q(pk__in=[]) if pk is None else Q(pk=pk)
    if by_text:
        found |= Q(path__icontains=text) | Q(user_id__icontains=text)
        found |= Q(user_agent__icontains=text)
    return found


COUNTS_SECONDS = 60


def _tab_counts(
    filters: Filters, filtered: QuerySet[Request], search: str
) -> dict[str | None, int]:

    def count() -> dict[str | None, int]:
        if (extent := filters.org_wide) is not None and not search:
            found = org_series(extent, filters.model, filters.period, requests=filters.requests)
            requests, failed, outcomes = found.total.requests, found.total.failed, found.outcomes
        else:
            counts = filtered.aggregate(
                requests=Count("id"), failed=Count("id", filter=FAILED), **OUTCOME_COUNTS
            )
            requests, failed = counts["requests"], counts["failed"]
            outcomes = {o: counts[o.name] for o in Outcome}
        return {None: requests, FAILED_TAB: failed} | {o.codes: n for o, n in outcomes.items()}

    period = filters.period
    scope = (filters.visibility, period.start.isoformat(), period.end.isoformat(), search)
    key = "request-log-tab-counts:" + hashlib.sha256(repr(scope).encode()).hexdigest()
    found: dict[str | None, int] | None = cache.get(key)
    if found is None:
        found = count()
        cache.set(key, found, COUNTS_SECONDS)
    return found


def _nearest(listed: QuerySet[Request], cursor: Cursor | None, *, older: bool) -> list[Request]:
    if older:
        found = listed.order_by("-timestamp", "-id")
        found = found.filter(cursor.older) if cursor else found
    else:
        found = listed.order_by("timestamp", "id")
        found = found.filter(cursor.newer) if cursor else found
    return list(found[: PAGE_SIZE + 1])


def _page_by_time(
    listed: QuerySet[Request],
    newest_first: bool,
    params: Mapping[str, str],
    open_id: int | None,
    count: int,
) -> tuple[list[Request], Pager]:
    forward, backward = ("before", "after") if newest_first else ("after", "before")
    before, after = Cursor.parse(params.get("before")), Cursor.parse(params.get("after"))
    cursor, side = (before, "before") if before else (after, "after")
    last = cursor is None and params.get("page") == LAST
    if cursor is not None and side == backward:
        found = _nearest(listed, cursor, older=not newest_first)
        if len(found) <= PAGE_SIZE:
            cursor = None

    start: int | None
    if last:
        found = _nearest(listed, None, older=not newest_first)
        page, has_previous, has_next = found[PAGE_SIZE - 1 :: -1], len(found) > PAGE_SIZE, False
        start = count - len(page) + 1
    elif cursor is not None and side == backward:
        page, has_previous, has_next = found[PAGE_SIZE - 1 :: -1], True, True
        start = None if cursor.rank is None else cursor.rank - PAGE_SIZE
    else:
        found = _nearest(listed, cursor, older=newest_first)
        page, has_previous, has_next = found[:PAGE_SIZE], cursor is not None, len(found) > PAGE_SIZE
        start = 1 if cursor is None else None if cursor.rank is None else cursor.rank + 1
        if cursor is None and open_id is not None and all(r.id != open_id for r in page):
            anchor = listed.prefetch_related(None).filter(pk=open_id).only("timestamp").first()
            if anchor is not None:
                cursor = Cursor.at(anchor).just_before(newest_first)
                found = _nearest(listed, cursor, older=newest_first)
                page, has_previous, has_next = found[:PAGE_SIZE], True, len(found) > PAGE_SIZE
                start = None

    if start is None and page and not has_next:
        start = count - len(page) + 1
    if start is not None and not (start >= 1 and start + len(page) - 1 <= max(count, 1)):
        start = None
    if last:
        here = Place(page=LAST)
    elif page and has_previous:
        here = Place(**{forward: str(Cursor.at(page[0], start).just_before(newest_first))})
    elif cursor is not None:
        here = Place(**{side: str(cursor)})
    else:
        here = Place()
    end = None if start is None else start + len(page) - 1
    return page, Pager(
        here=here,
        first=Place() if has_previous else None,
        previous=(
            (Place(**{backward: str(Cursor.at(page[0], start))}) if page else Place())
            if has_previous
            else None
        ),
        next=Place(**{forward: str(Cursor.at(page[-1], end))}) if has_next else None,
        last=Place(page=LAST) if has_next else None,
        start=start,
    )


def _page_by_offset(
    listed: QuerySet[Request], sort: Sort, raw_page: str | None, count: int
) -> tuple[list[Request], Pager]:
    pages = max(1, -(-count // PAGE_SIZE))
    number = pages if raw_page == LAST else min(max(parse_id(raw_page) or 1, 1), _MAX_PAGE)
    offset = (number - 1) * PAGE_SIZE
    found = list(listed.order_by(*sort.ordering)[offset : offset + PAGE_SIZE + 1])
    page, has_next = found[:PAGE_SIZE], len(found) > PAGE_SIZE or number < pages

    def at(n: int) -> Place:
        return Place() if n == 1 else Place(page=str(n))

    return page, Pager(
        here=at(number),
        first=at(1) if number > 1 else None,
        previous=at(number - 1) if number > 1 else None,
        next=at(number + 1) if has_next else None,
        last=Place(page=LAST) if number < pages else None,
        start=offset + 1 if offset + len(page) <= count else None,
    )


def request_log(filters: Filters, params: Mapping[str, str]) -> RequestLog:
    status = raw if (raw := params.get("status")) in _STATUS_FILTERS else None
    search = params.get("q", "").strip()[:SEARCH_MAX]
    free = bounded_scan(filters)
    sort = Sort.parse(params.get("sort", "")) or NEWEST
    if not (free or sort.column is Column.TIME):
        sort = NEWEST
    open_id = parse_id(params.get("request"))

    filtered = filters.period.of(filters.requests)
    if search:
        filtered = filtered.filter(request_search(search, by_text=free))
    counts = _tab_counts(filters, filtered, search)
    tabs = (
        Tab(None, gettext("All"), counts[None]),
        Tab(FAILED_TAB, gettext("Failed"), counts[FAILED_TAB]),
        *(Tab(o.codes, str(o.label), counts[o.codes], o.codes) for o in Outcome),
    )

    if status is not None:
        filtered = filtered.filter(_STATUS_FILTERS[status])
    listed = filtered.prefetch_related("token__user__profile", "token__service_account__team")
    if sort.column is Column.TIME:
        page, pager = _page_by_time(listed, sort.descending, params, open_id, counts[status])
    else:
        page, pager = _page_by_offset(listed, sort, params.get("page"), counts[status])

    rows = tuple(
        Row(request, describe_key(request.token, filters.viewer), request.id == open_id)
        for request in page
    )
    return RequestLog(
        rows=rows,
        tabs=tabs,
        count=counts[status],
        status=status,
        search=search,
        searches_text=free,
        sort=sort,
        headers=_headers(sort, free),
        pager=pager,
        shows_key=filters.org_wide is not None or (filters.key is None and len(filters.keys) > 1),
        shows_overhead=filters.can_pick_scope,
    )
