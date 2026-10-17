from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from django.conf import settings
from django.core.paginator import Page, Paginator
from django.db.models import Count, Min, Q, QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.http import urlencode

from management.key_activity import FAILED, router_models, served
from management.models import Org, Request, Team, Token, UserProfile
from management.usage_report import NO_ORG, STATUSES, Period, Span, duration

PAGE_SIZE = 50


def chosen_org(profile: UserProfile, raw: str | None) -> Org | None:
    """The org whose requests the viewer looks at. `None` means every org, for admins only."""
    if not profile.is_admin():
        return profile.org
    if raw == "all":
        return None
    if raw and raw.isdigit():
        return Org.objects.filter(pk=int(raw)).first() or profile.org
    return profile.org


def visible_tokens(profile: UserProfile, org: Org | None, teams: QuerySet[Team]) -> QuerySet[Token]:
    """Keys whose requests the viewer may see in `org`: every key for an org admin, else their
    own keys and their teams' service accounts. `org=None` means every org."""
    if org is None:
        return Token.objects.all()
    if profile.is_org_admin(org):
        return Token.objects.filter(Q(user__profile__org=org) | Q(service_account__team__org=org))
    return Token.objects.filter(Q(user=profile.user) | Q(service_account__team__in=teams))


def visible_requests(
    profile: UserProfile, org: Org | None, teams: QuerySet[Team]
) -> QuerySet[Request]:
    """Requests the viewer may see in `org`, limited to the models the router serves.

    Besides the requests of `visible_tokens`, a user also sees requests that name their email
    as `user_id` on another key of their org.
    """
    if org is None:
        return served(Request.objects.all())
    requests = Request.objects.filter(token__in=visible_tokens(profile, org, teams))
    if not profile.is_org_admin(org):
        user = profile.user
        requests |= Request.objects.filter(
            user_id=user.email, token__user__profile__org=profile.org
        )
    return served(requests)


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
    and time frame. `requests` holds the visible requests narrowed by all but the time frame."""

    orgs: tuple[Org, ...]
    org: Org | None
    can_pick_scope: bool
    mine: bool
    keys: tuple[Key, ...]
    key: Key | None
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
        if self.orgs:
            found["org"] = str(self.org.id) if self.org else "all"
        if self.mine:
            found["scope"] = "mine"
        if self.key:
            found["key"] = str(self.key.token.id)
        if self.model:
            found["model"] = self.model
        found["span"] = self.span.value
        return found

    def log_url(self, **narrower: str) -> str:
        """The request log with this selection, narrowed or overridden by `narrower`."""
        return f"{reverse('requests')}?{urlencode(self.query | narrower)}"


def read_filters(
    profile: UserProfile,
    teams: QuerySet[Team],
    params: Mapping[str, str],
    *,
    default_span: Span,
    now: datetime,
) -> Filters:
    """Parse the shared filters. A key outside what the viewer may see is a 404."""
    org = chosen_org(profile, params.get("org"))
    sees_others = org is None or profile.is_org_admin(org)
    scope = visible_requests(profile, org, teams)
    team_ids = set(teams.values_list("id", flat=True))
    requests = scope
    mine = sees_others and params.get("scope") == "mine"
    if mine:
        requests = requests.filter(
            Q(token__user=profile.user, token__service_account__isnull=True)
            | Q(token__service_account__team__in=profile.teams.all())
        )

    tokens = Token.objects.select_related(
        "user__profile__org", "service_account__team__org"
    ).order_by("name", "id")

    def describe(token: Token) -> Key:
        return describe_key(token, profile, team_ids, with_org=org is None)

    keys = tuple(describe(token) for token in tokens.filter(id__in=requests.values("token_id")))
    key = None
    if raw_key := params.get("key", ""):
        if not raw_key.isdigit():
            raise Http404
        allowed = Q(id__in=scope.values("token_id")) | Q(
            id__in=visible_tokens(profile, org, teams).values("id")
        )
        key = describe(get_object_or_404(tokens.filter(allowed), pk=int(raw_key)))
        if key.token not in (k.token for k in keys):
            keys = (key, *keys)
        requests = requests.filter(token=key.token)
    models = tuple(sorted(router_models()))
    model = params.get("model") if params.get("model") in models else None
    if model is not None:
        requests = requests.filter(model=model)

    span = Span.parse(params.get("span", "")) or default_span
    oldest = now
    if span is Span.ALL:
        retained = now - timedelta(days=getattr(settings, "REQUEST_RETENTION_DAYS", 30))
        first = requests.aggregate(first=Min("timestamp"))["first"]
        oldest = min(first or retained, retained)
    period = Period.last(span, now, oldest)

    return Filters(
        orgs=tuple(Org.objects.order_by("name")) if profile.is_admin() else (),
        org=org,
        can_pick_scope=sees_others,
        mine=mine,
        keys=keys,
        key=key,
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
class RequestLog:
    page: Page
    rows: tuple[Row, ...]
    tabs: tuple[Tab, ...]


def request_log(
    profile: UserProfile,
    teams: QuerySet[Team],
    filters: Filters,
    *,
    outcome: Outcome | None,
    page_number: str | None,
    open_id: int | None,
) -> RequestLog:
    team_ids = set(teams.values_list("id", flat=True))
    filtered = filters.period.of(filters.requests)

    counts = filtered.aggregate(
        total=Count("id"), **{o.name: Count("id", filter=o.matches) for o in Outcome}
    )
    tabs = (Tab(None, "All", counts["total"]), *(Tab(o, o.label, counts[o.name]) for o in Outcome))

    if outcome is not None:
        filtered = filtered.filter(outcome.matches)
    paginator = Paginator(
        filtered.select_related("token__user", "token__service_account__team").order_by(
            "-timestamp", "-id"
        ),
        PAGE_SIZE,
    )
    paginator.count = counts[outcome.name if outcome else "total"]
    page = paginator.get_page(page_number)
    rows = tuple(
        Row(request, describe_key(request.token, profile, team_ids), request.id == open_id)
        for request in page
    )
    return RequestLog(page, rows, tabs)
