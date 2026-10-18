import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from django.contrib.sessions.backends.base import SessionBase
from django.core.cache import cache
from django.db.models import Count, F, Q, QuerySet
from django.db.models.expressions import CombinedExpression, OrderBy
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from management.key_activity import FAILED, KeyAccess, grouped, of_keys, router_models, served
from management.model_catalog import scopes_for, usable_models
from management.models import Org, Request, Team, Token, UserProfile
from management.models.token import of_org
from management.usage_report import NO_ORG, STATUSES, Period, Span, duration
from management.usage_rollup import org_series

PAGE_SIZE = 50
_MAX_ID = 2**63 - 2  # the largest bigint that `Cursor.just_before` can still add one to
_MAX_DIGITS = len(str(_MAX_ID))


def parse_id(raw: str | None) -> int | None:
    """A database id from a URL parameter, or `None` for anything else."""
    if not (raw and len(raw) <= _MAX_DIGITS and raw.isascii() and raw.isdigit()):
        return None
    if int(raw) > _MAX_ID:
        return None
    return int(raw)


SESSION_ORG = "org"
ORG_BOUND_PARAMS = ("org", "key", "key_q", "before", "after", "request", "page")
"""The URL parameters that name something of one org, which a page drops on a change of org."""


def remember_org(session: SessionBase, org: Org | None) -> None:
    value = "all" if org is None else str(org.pk)
    if session.get(SESSION_ORG) != value:
        session[SESSION_ORG] = value


def chosen_org(profile: UserProfile, request: HttpRequest) -> Org | None:
    """The org the viewer looks at, `None` for every org. An admin chooses it with the org switch
    or a `?org=` link, an org id or `all`, and the session keeps it for later pages. Everyone
    else always sees their own org."""
    if not profile.is_admin():
        return profile.org
    raw = request.GET.get("org", request.session.get(SESSION_ORG))
    org: Org | None = None
    if raw != "all":
        org_id = parse_id(raw)
        org = Org.objects.filter(pk=org_id).first() if org_id is not None else None
        if org is None:
            return profile.org
    remember_org(request.session, org)
    return org


def visible_tokens(profile: UserProfile, org: Org | None, teams: QuerySet[Team]) -> QuerySet[Token]:
    """Keys whose requests the viewer may see in `org`: every key for an org admin, else their
    personal keys and their teams' service accounts. `org=None` means every org."""
    if org is None:
        return Token.objects.all()
    if profile.is_org_admin(org):
        return Token.objects.filter(of_org(org))
    return Token.objects.filter(
        Q(user=profile.user, service_account__isnull=True) | Q(service_account__team__in=teams)
    )


def visible_requests(
    profile: UserProfile, org: Org | None, teams: QuerySet[Team]
) -> QuerySet[Request]:
    """Requests of the `visible_tokens`, limited to the models the router serves."""
    if org is None:
        return served(Request.objects.all())
    return served(of_keys(Request.objects.all(), visible_tokens(profile, org, teams)))


_OVERVIEW_STATUS = {
    codes: (label, icon, tone, match) for label, codes, icon, tone, match in STATUSES
}


class Outcome(StrEnum):
    """The Overview page's status groups, as filters of the request log. `ERRORS` is every
    failed request, as the Overview page's Failed total counts them."""

    ERRORS = "failed"
    OK = "2xx"
    RATE_LIMITED = "429"
    REFUSED = "4xx"
    FAILED = "5xx"

    @property
    def label(self) -> str:
        return gettext("Failed") if self is Outcome.ERRORS else str(_OVERVIEW_STATUS[self.value][0])

    @property
    def codes(self) -> str | None:
        """The status codes of a group, `None` for `ERRORS`, which spans several groups."""
        return None if self is Outcome.ERRORS else self.value

    @property
    def matches(self) -> Q:
        return FAILED if self is Outcome.ERRORS else _OVERVIEW_STATUS[self.value][3]

    @property
    def tone(self) -> str:
        return _OVERVIEW_STATUS[self.value][2]

    @property
    def icon(self) -> str:
        return _OVERVIEW_STATUS[self.value][1]

    @classmethod
    def parse(cls, raw: str) -> "Outcome | None":
        try:
            return cls(raw)
        except ValueError:
            return None

    @classmethod
    def of(cls, status_code: int | None) -> "Outcome | None":
        if status_code is None:
            return None
        if status_code < HTTPStatus.BAD_REQUEST:
            return cls.OK
        if status_code == HTTPStatus.TOO_MANY_REQUESTS:
            return cls.RATE_LIMITED
        if status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
            return cls.REFUSED
        return cls.FAILED


_ERROR_MEANINGS = {
    Outcome.RATE_LIMITED: _("The key or the provider was over a limit."),
    Outcome.REFUSED: _(
        "The request was not valid, for example an unknown model or a malformed body."
    ),
    Outcome.FAILED: _("The model or the provider failed or timed out."),
}


@dataclass(frozen=True)
class Key:
    token: Token
    href: str | None
    note: str
    own: bool = False
    """Whether it is the viewer's personal key."""

    @property
    def name(self) -> str:
        return self.token.name


def describe_key(token: Token, access: KeyAccess, *, with_org: bool = False) -> Key:
    """How the log names a key: a link when the viewer may open it, whose key it is, and, with
    `with_org`, its org. Load the relations `KeyAccess.can_open` reads, or each key costs
    queries."""
    href = reverse("token_detail", args=[token.id]) if access.can_open(token) else None
    account, own = token.service_account, False
    if account is not None:
        note = gettext("Service account · %(team)s") % {"team": account.team.name}
    elif token.user_id == access.user_id:
        note, own = gettext("Personal"), True
    else:
        note = token.user.email
    if with_org:
        owner = account.team if account is not None else getattr(token.user, "profile", None)
        note += f" · {owner.org.name if owner is not None else NO_ORG}"
    return Key(token, href, note, own)


@dataclass(frozen=True)
class Filters:
    """The selection the Overview page and the request log share: org, whose keys, key, model,
    and time frame. `requests` holds the visible requests narrowed by all but the time frame.
    `visibility` names everything that decides which requests `requests` holds, for cache keys.

    The key picker lists `keys`, the viewer's own keys, and `matches`, the visible keys that
    match the search `key_q`. `only_key` is the one key the viewer may see, when they see just
    one, so that there is no key to choose. `access` decides which keys link to their pages.
    `page_params` holds the page's own parameters, such
    as the log's status tab, which the key search keeps."""

    can_pick_org: bool
    org: Org | None
    can_pick_scope: bool
    mine: bool
    keys: tuple[Key, ...]
    only_key: Key | None
    key: Key | None
    key_q: str
    matches: tuple[Key, ...]
    access: KeyAccess
    page_params: tuple[tuple[str, str], ...]
    models: tuple[str, ...]
    model: str | None
    span: Span
    period: Period
    requests: QuerySet[Request]
    visibility: tuple[object, ...]

    @property
    def spans(self) -> tuple[Span, ...]:
        return tuple(Span)

    @property
    def query(self) -> dict[str, str]:
        found = {}
        if self.can_pick_org:
            found["org"] = str(self.org.id) if self.org else "all"
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
        """What the key search submits besides `key_q`."""
        return tuple((dict(self.page_params) | self.query).items())

    def log_url(self, **narrower: str) -> str:
        """The request log with this selection, narrowed or overridden by `narrower`."""
        return f"{reverse('requests')}?{urlencode(self.query | narrower)}"


KEY_MATCHES = 20
_NOT_PAGE_PARAMS = frozenset(
    {"org", "scope", "key", "model", "span", "key_q", "before", "after", "request", "page"}
)


def key_search(text: str, *, by_owner: bool) -> Q:
    """Keys whose name holds `text`, or, with `by_owner`, whose owner's email does. Only a
    viewer who sees the whole org may search by owner, or they would learn who created a
    service account's key."""
    found = Q(name__icontains=text)
    return found | Q(user__email__icontains=text) if by_owner else found


def read_filters(
    profile: UserProfile,
    teams: QuerySet[Team],
    params: Mapping[str, str],
    *,
    org: Org | None,
    default_span: Span,
    now: datetime,
) -> Filters:
    """Parse the shared filters of `org`, every org when `None`. A key outside what the viewer
    may see is a 404."""
    sees_others = org is None or profile.is_org_admin(org)
    requests = visible_requests(profile, org, teams)
    if sees_others:
        names = set(router_models())
    else:
        # A user's list: what their keys may use, and what their requests used.
        used = requests.order_by().values_list("model", flat=True).distinct()
        names = {*usable_models(scopes_for(profile)), *used}
    models = tuple(sorted(names, key=lambda name: (name.casefold(), name)))
    team_ids = set(teams.values_list("id", flat=True))
    tokens = (
        visible_tokens(profile, org, teams)
        .select_related("user__profile__org", "service_account__team__org")
        .order_by("name", "id")
    )

    access = KeyAccess.of(profile)

    def describe(token: Token) -> Key:
        return describe_key(token, access, with_org=org is None)

    own = Q(user=profile.user, service_account__isnull=True) | Q(
        service_account__team__in=profile.teams.all()
    )
    keys = tuple(describe(token) for token in tokens.filter(own))
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

    if org is None:
        viewer: tuple[object, ...] = ("all orgs",)
    elif sees_others:
        viewer = ("org", org.pk)
    else:
        viewer = ("keys of", org.pk, profile.user_id, *sorted(team_ids))
    narrowed = (
        ("mine", profile.user_id, *(k.token.id for k in keys)) if mine else (),
        key.token.id if key else None,
        model,
    )

    return Filters(
        can_pick_org=profile.is_admin(),
        org=org,
        can_pick_scope=sees_others,
        mine=mine,
        keys=keys,
        only_key=only_key,
        key=key,
        key_q=key_q,
        matches=matches,
        access=access,
        page_params=tuple(
            (name, value) for name, value in params.items() if name not in _NOT_PAGE_PARAMS
        ),
        models=models,
        model=model,
        span=span,
        period=period,
        requests=requests,
        visibility=(viewer, *narrowed),
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
    def status_phrase(self) -> str:
        try:
            return HTTPStatus(self.request.status_code or 0).phrase
        except ValueError:
            return ""

    @property
    def error(self) -> str | None:
        """What a failed request's status means, `None` for a successful one."""
        outcome = self.outcome
        if outcome is None:
            return gettext("No status was recorded.")
        if outcome is Outcome.OK:
            return None
        phrase = self.status_phrase
        meaning = str(_ERROR_MEANINGS[outcome])
        return f"{self.request.status_code} {phrase}. {meaning}" if phrase else meaning

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
    outcome: Outcome | None
    label: str
    count: int

    @property
    def value(self) -> str | None:
        return self.outcome.value if self.outcome else None


class Column(StrEnum):
    """A column the log sorts by. `TIME` pages by cursor. The others page by offset, which
    sorts every matching request, so the log offers them only on a `bounded_scan`."""

    TIME = "time"
    MODEL = "model"
    STATUS = "status"
    TOKENS = "tokens"
    RESPONSE = "response"

    @property
    def expression(self) -> F | CombinedExpression:
        """What the column sorts on. `TOKENS` is the total, input plus output tokens."""
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
        """The direction of a first sort: newest, worst, largest and slowest first, models A-Z."""
        return self is not Column.MODEL


@dataclass(frozen=True)
class Sort:
    """An order of the log. In a URL it reads `<column>`, or `-<column>` for descending."""

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
        """Ties are broken by id in the same direction, and missing values come last."""
        nulls_last = True if self.column in (Column.STATUS, Column.RESPONSE) else None
        if self.descending:
            return self.column.expression.desc(nulls_last=nulls_last), F("id").desc()
        return self.column.expression.asc(nulls_last=nulls_last), F("id").asc()

    @property
    def param(self) -> str | None:
        """The `sort` URL value, `None` for the default order."""
        return None if self == NEWEST else str(self)


NEWEST = Sort(Column.TIME, descending=True)
BOUNDED_SPAN = timedelta(days=7)


def bounded_scan(filters: Filters) -> bool:
    """Whether the log may read every request of the selection, to sort by any column or search
    their text: the selection spans at most `BOUNDED_SPAN`, is one key or one model, or is no
    wider than the viewer's own keys and teams. Sorting by time and finding a request by id
    stay cheap on any selection, because they read an index."""
    return (
        not counts_whole_org(filters)
        or filters.model is not None
        or filters.period.end - filters.period.start <= BOUNDED_SPAN
    )


@dataclass(frozen=True)
class Header:
    """A sortable column heading. `choice` is the `sort` value a click sets; `aria_sort` is
    `ascending` or `descending` on the column the log is sorted by. A heading the selection is
    too wide to sort by has `sortable` false."""

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
    """A place in the log, at the request at `timestamp` with `id`. `rank` is that request's
    1-based position in the log's order, when known. In a URL it reads
    `<microseconds since the epoch>,<id>`, with `,<rank>` when the rank is known."""

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
        """Requests older than this one. The first term lets Postgres range-scan an index on
        the timestamp."""
        return Q(timestamp__lte=self.timestamp) & (
            Q(timestamp__lt=self.timestamp) | Q(id__lt=self.id)
        )

    @property
    def newer(self) -> Q:
        return Q(timestamp__gte=self.timestamp) & (
            Q(timestamp__gt=self.timestamp) | Q(id__gt=self.id)
        )

    def just_before(self, newest_first: bool) -> "Cursor":
        """The cursor whose next page, in a log sorted `newest_first` or oldest first, starts
        with this request."""
        rank = None if self.rank is None else self.rank - 1
        return Cursor(self.timestamp, self.id + (1 if newest_first else -1), rank)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LAST = "last"


@dataclass(frozen=True)
class Place:
    """The URL parameters that open a page of the log. At most one is set, and none opens
    the first page. `before` and `after` are cursors, older or newer than which the page
    lists requests; `page` is a page number, or `last`."""

    before: str | None = None
    after: str | None = None
    page: str | None = None


@dataclass(frozen=True)
class Pager:
    """Where a page sits. `here` reopens it, and the links are `None` where there is no page
    to go to. `start` is the position of the page's first row, `None` when unknown."""

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
    """One page of the log, of the requests in the chosen tab that match `search`, by id alone
    unless `searches_text`. `count` is the number of them. `shows_key` is false when every row
    is of the same key, and `shows_overhead` is true for a viewer who sees the whole org."""

    rows: tuple[Row, ...]
    tabs: tuple[Tab, ...]
    count: int
    outcome: Outcome | None
    search: str
    searches_text: bool
    sort: Sort
    headers: Mapping[str, Header]
    pager: Pager
    shows_key: bool
    shows_overhead: bool

    @property
    def text_blocked(self) -> bool:
        """Whether the search is text the selection is too wide to search."""
        return bool(self.search) and not self.searches_text and parse_id(self.search) is None

    @property
    def end(self) -> int | None:
        start = self.pager.start
        return None if start is None else start + len(self.rows) - 1


def counts_whole_org(filters: Filters) -> bool:
    """Whether the selection is every request of its org, or of every org, narrowed at most by
    model, as the usage rollups count them."""
    return filters.can_pick_scope and not filters.mine and filters.key is None


SEARCH_MAX = 256


def request_search(text: str, *, by_text: bool) -> Q:
    """Requests whose id is `text`, or, with `by_text`, whose path, user ID sent by the client,
    or user agent holds it. No index serves the text: only a `bounded_scan` may search it."""
    pk = parse_id(text)
    found = Q(pk__in=[]) if pk is None else Q(pk=pk)
    if by_text:
        found |= Q(path__icontains=text) | Q(user_id__icontains=text)
        found |= Q(user_agent__icontains=text)
    return found


COUNTS_SECONDS = 60


def _tab_counts(
    filters: Filters, filtered: QuerySet[Request], search: str
) -> dict[Outcome | None, int]:
    """The requests in each tab, `None` for All, cached for `COUNTS_SECONDS` under everything
    that decides them, so paging and sorting do not count again. An org-wide selection without
    a search reads the rollups."""

    def count() -> dict[Outcome | None, int]:
        if counts_whole_org(filters) and not search:
            found = org_series(
                filters.org, filters.model, filters.period, requests=filters.requests
            )
            by_codes = zip((codes for _, codes, *_ in STATUSES), found.status_counts, strict=True)
            return {None: found.total.requests, Outcome.ERRORS: found.total.failed} | {
                Outcome(codes): n for codes, n in by_codes
            }
        counts = filtered.aggregate(
            total=Count("id"), **{o.name: Count("id", filter=o.matches) for o in Outcome}
        )
        return {None: counts["total"]} | {o: counts[o.name] for o in Outcome}

    period = filters.period
    scope = (filters.visibility, period.start.isoformat(), period.end.isoformat(), search)
    key = "request-log-tabs:" + hashlib.sha256(repr(scope).encode()).hexdigest()
    found: dict[Outcome | None, int] | None = cache.get(key)
    if found is None:
        found = count()
        cache.set(key, found, COUNTS_SECONDS)
    return found


def _nearest(listed: QuerySet[Request], cursor: Cursor | None, *, older: bool) -> list[Request]:
    """Up to `PAGE_SIZE + 1` requests older or newer than `cursor`, nearest first. Without a
    cursor, from the newest or the oldest end."""
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
    """A page of the log sorted by time, paged by cursor, never by offset. Without a cursor the
    page is the first one, or the page that starts at `open_id` when the first page does not
    hold it. Each link carries its rank, so the page knows its position without counting."""
    forward, backward = ("before", "after") if newest_first else ("after", "before")
    before, after = Cursor.parse(params.get("before")), Cursor.parse(params.get("after"))
    cursor, side = (before, "before") if before else (after, "after")
    last = cursor is None and params.get("page") == LAST
    if cursor is not None and side == backward:
        found = _nearest(listed, cursor, older=not newest_first)
        if len(found) <= PAGE_SIZE:  # less than a page before the cursor: the first page
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
    """A page of the log in another order than time, paged by offset. `count` may be up to
    `COUNTS_SECONDS` old, so a page past it still links to the next one when it holds more."""
    pages = max(1, -(-count // PAGE_SIZE))
    number = pages if raw_page == LAST else max(parse_id(raw_page) or 1, 1)
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
    """The page of the log that `params` asks for: its status tab (`status`), search
    (`q`), order (`sort`), place (`before`, `after` or `page`), and open request (`request`).
    An order the selection is too wide for falls back to newest first."""
    outcome = Outcome.parse(params.get("status", ""))
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
    tabs = (Tab(None, gettext("All"), counts[None]), *(Tab(o, o.label, counts[o]) for o in Outcome))

    if outcome is not None:
        filtered = filtered.filter(outcome.matches)
    listed = filtered.prefetch_related("token__user__profile", "token__service_account__team")
    if sort.column is Column.TIME:
        page, pager = _page_by_time(listed, sort.descending, params, open_id, counts[outcome])
    else:
        page, pager = _page_by_offset(listed, sort, params.get("page"), counts[outcome])

    rows = tuple(
        Row(request, describe_key(request.token, filters.access), request.id == open_id)
        for request in page
    )
    return RequestLog(
        rows=rows,
        tabs=tabs,
        count=counts[outcome],
        outcome=outcome,
        search=search,
        searches_text=free,
        sort=sort,
        headers=_headers(sort, free),
        pager=pager,
        shows_key=counts_whole_org(filters) or (filters.key is None and len(filters.keys) > 1),
        shows_overhead=filters.can_pick_scope,
    )
