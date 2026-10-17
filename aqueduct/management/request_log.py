from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from django.contrib.sessions.backends.base import SessionBase
from django.db.models import Count, Q, QuerySet
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import urlencode

from management.key_activity import FAILED, of_keys, router_models, served
from management.models import Org, Request, Team, Token, UserProfile
from management.models.token import of_org
from management.usage_report import NO_ORG, STATUSES, Period, Span, duration
from management.usage_rollup import org_series

PAGE_SIZE = 50
_MAX_ID = 2**63 - 2  # the largest bigint that `Cursor.just_before` can still add one to


def parse_id(raw: str | None) -> int | None:
    """A database id from a URL parameter, or `None` for anything else."""
    if not (raw and raw.isascii() and raw.isdigit()) or int(raw) > _MAX_ID:
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
        return {Outcome.ERRORS: "Failed", Outcome.REFUSED: "Other 4xx"}.get(self, self.value)

    @property
    def codes(self) -> str | None:
        """The status codes of a group whose label does not name them."""
        return "4xx and 5xx" if self is Outcome.ERRORS else None

    @property
    def matches(self) -> Q:
        return FAILED if self is Outcome.ERRORS else _OVERVIEW_STATUS[self.value][3]

    @property
    def meaning(self) -> str:
        return {
            Outcome.OK: "The request succeeded.",
            Outcome.RATE_LIMITED: "Rate-limited: the key or the provider was over a limit.",
            Outcome.REFUSED: "The request was not valid, for example an unknown model "
            "or a malformed body.",
            Outcome.FAILED: "The model or the provider failed or timed out.",
        }[self]

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


@dataclass(frozen=True)
class Key:
    token: Token
    href: str | None
    note: str

    @property
    def name(self) -> str:
        return self.token.name


def describe_key(
    token: Token, profile: UserProfile, team_ids: Collection[int], *, with_org: bool = False
) -> Key:
    """How the log names a key: a link when the viewer may open it, whose key it is, and, with
    `with_org`, its org."""
    account = token.service_account
    if account is not None:
        href = reverse("token_detail", args=[token.id]) if account.team_id in team_ids else None
        note = f"Service account · {account.team.name}"
    elif token.user_id == profile.user_id:
        href, note = reverse("token_detail", args=[token.id]), "Personal"
    else:
        href, note = None, token.user.email
    if with_org:
        owner = account.team if account is not None else getattr(token.user, "profile", None)
        note += f" · {owner.org.name if owner is not None else NO_ORG}"
    return Key(token, href, note)


@dataclass(frozen=True)
class Filters:
    """The selection the Overview page and the request log share: org, whose keys, key, model,
    and time frame. `requests` holds the visible requests narrowed by all but the time frame.

    The key picker lists `keys`, the viewer's own keys, and `matches`, the visible keys that
    match the search `key_q`. `page_params` holds the page's own parameters, such as the
    log's status tab, which the key search keeps."""

    can_pick_org: bool
    org: Org | None
    can_pick_scope: bool
    mine: bool
    keys: tuple[Key, ...]
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
    team_ids = set(teams.values_list("id", flat=True))
    tokens = (
        visible_tokens(profile, org, teams)
        .select_related("user__profile__org", "service_account__team__org")
        .order_by("name", "id")
    )

    def describe(token: Token) -> Key:
        return describe_key(token, profile, team_ids, with_org=org is None)

    own = Q(user=profile.user, service_account__isnull=True) | Q(
        service_account__team__in=profile.teams.all()
    )
    keys = tuple(describe(token) for token in tokens.filter(own))
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

    models = tuple(sorted(router_models()))
    model = params.get("model") if params.get("model") in models else None
    if model is not None:
        requests = requests.filter(model=model)

    span = Span.parse(params.get("span", "")) or default_span
    period = Period.last(span, now)

    return Filters(
        can_pick_org=profile.is_admin(),
        org=org,
        can_pick_scope=sees_others,
        mine=mine,
        keys=keys,
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
    def status_phrase(self) -> str:
        try:
            return HTTPStatus(self.request.status_code or 0).phrase
        except ValueError:
            return ""

    @property
    def meaning(self) -> str:
        outcome = self.outcome
        if outcome is not None:
            return outcome.meaning
        return "No status was recorded." if self.request.status_code is None else ""

    @property
    def response_time(self) -> str:
        return duration(self.request.response_time_ms)

    @property
    def overhead(self) -> str:
        return duration(self.request.processing_time_ms)

    @property
    def output_speed(self) -> str:
        tokens, ms = self.request.output_tokens, self.request.response_time_ms
        if not tokens or not ms:
            return "n/a"
        return f"{round(tokens * 1000 / ms):,} tok/s"


@dataclass(frozen=True)
class Tab:
    outcome: Outcome | None
    label: str
    count: int

    @property
    def value(self) -> str | None:
        return self.outcome.value if self.outcome else None


@dataclass(frozen=True)
class Cursor:
    """A place in the log, newest first: the request at `timestamp` with `id`. In a URL it
    reads `<microseconds since the epoch>,<id>`."""

    timestamp: datetime
    id: int

    @classmethod
    def parse(cls, raw: str | None) -> "Cursor | None":
        micros, _, raw_id = (raw or "").partition(",")
        pk = parse_id(raw_id)
        if pk is None or not (micros.isascii() and micros.isdigit()):
            return None
        try:
            return cls(_EPOCH + timedelta(microseconds=int(micros)), pk)
        except OverflowError:
            return None

    @classmethod
    def at(cls, request: Request) -> "Cursor":
        return cls(request.timestamp, request.id)

    def __str__(self) -> str:
        return f"{(self.timestamp - _EPOCH) // timedelta(microseconds=1)},{self.id}"

    @property
    def older(self) -> Q:
        """Requests after this one in the log. The first term lets Postgres range-scan an index
        on the timestamp."""
        return Q(timestamp__lte=self.timestamp) & (
            Q(timestamp__lt=self.timestamp) | Q(id__lt=self.id)
        )

    @property
    def newer(self) -> Q:
        return Q(timestamp__gte=self.timestamp) & (
            Q(timestamp__gt=self.timestamp) | Q(id__gt=self.id)
        )

    @property
    def just_before(self) -> "Cursor":
        """The cursor whose `older` requests start with this one."""
        return Cursor(self.timestamp, self.id + 1)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class RequestLog:
    """One page of the log. `here` reproduces the page as a `before` cursor, `None` on the
    newest page; `newer` and `older` are the `after` and `before` cursors of the pages next to
    it, `None` at either end. The cursors are URL values. `count` is the number of requests in
    the chosen tab."""

    rows: tuple[Row, ...]
    tabs: tuple[Tab, ...]
    count: int
    here: str | None
    newer: str | None
    older: str | None


def counts_whole_org(filters: Filters) -> bool:
    """Whether the selection is every request of its org, or of every org, narrowed at most by
    model, as the usage rollups count them."""
    return filters.can_pick_scope and not filters.mine and filters.key is None


def _tab_counts(filters: Filters, filtered: QuerySet[Request]) -> dict[Outcome | None, int]:
    """The requests in each tab, `None` for All. An org-wide selection reads the rollups."""
    if counts_whole_org(filters):
        found = org_series(filters.org, filters.model, filters.period, requests=filters.requests)
        by_codes = zip((codes for _, codes, *_ in STATUSES), found.status_counts, strict=True)
        return {None: found.total.requests, Outcome.ERRORS: found.total.failed} | {
            Outcome(codes): count for codes, count in by_codes
        }
    counts = filtered.aggregate(
        total=Count("id"), **{o.name: Count("id", filter=o.matches) for o in Outcome}
    )
    return {None: counts["total"]} | {o: counts[o.name] for o in Outcome}


def request_log(
    profile: UserProfile,
    teams: QuerySet[Team],
    filters: Filters,
    *,
    outcome: Outcome | None,
    before: Cursor | None,
    after: Cursor | None,
    open_id: int | None,
) -> RequestLog:
    """A page of the log, paged by cursor, never by offset. Without a cursor the page is the
    newest one, or the page that starts at `open_id` when the newest page does not hold it."""
    team_ids = set(teams.values_list("id", flat=True))
    filtered = filters.period.of(filters.requests)

    counts = _tab_counts(filters, filtered)
    tabs = (Tab(None, "All", counts[None]), *(Tab(o, o.label, counts[o]) for o in Outcome))

    if outcome is not None:
        filtered = filtered.filter(outcome.matches)
    listed = filtered.prefetch_related("token__user", "token__service_account__team")
    newest_first = listed.order_by("-timestamp", "-id")

    def older_than(cursor: Cursor | None) -> tuple[list[Request], bool]:
        found = list(
            (newest_first.filter(cursor.older) if cursor else newest_first)[: PAGE_SIZE + 1]
        )
        return found[:PAGE_SIZE], len(found) > PAGE_SIZE

    newer = []
    if after is not None and before is None:
        newer = list(listed.filter(after.newer).order_by("timestamp", "id")[: PAGE_SIZE + 1])
    if len(newer) > PAGE_SIZE:
        page, has_older = newer[PAGE_SIZE - 1 :: -1], True
        here: Cursor | None = Cursor.at(page[0]).just_before
    else:
        here = before
        page, has_older = older_than(here)
    if here is None and open_id is not None and all(r.id != open_id for r in page):
        anchor = filtered.filter(pk=open_id).only("timestamp").first()
        if anchor is not None:
            here = Cursor.at(anchor).just_before
            page, has_older = older_than(here)

    rows = tuple(
        Row(request, describe_key(request.token, profile, team_ids), request.id == open_id)
        for request in page
    )
    return RequestLog(
        rows=rows,
        tabs=tabs,
        count=counts[outcome],
        here=str(here) if here else None,
        newer=str(Cursor.at(rows[0].request)) if here and rows else None,
        older=str(Cursor.at(rows[-1].request)) if has_older else None,
    )
