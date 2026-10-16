from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus

from django.core.paginator import Page, Paginator
from django.db.models import Count, Q, QuerySet
from django.urls import reverse

from gateway.config import get_router_config
from management.models import Org, Request, Team, Token, UserProfile
from management.usage_report import NO_ORG, STATUSES, duration

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
        return Request.objects.filter(model__in=router_models())
    requests = Request.objects.filter(token__in=visible_tokens(profile, org, teams))
    if not profile.is_org_admin(org):
        user = profile.user
        requests |= Request.objects.filter(
            user_id=user.email, token__user__profile__org=profile.org
        )
    return requests.filter(model__in=router_models())


def router_models() -> list[str]:
    return [entry["model_name"] for entry in get_router_config().get("model_list", [])]


_OVERVIEW_STATUS = {
    codes: (label, icon, tone, match) for label, codes, icon, tone, match in STATUSES
}


class Outcome(StrEnum):
    """The Overview page's status groups, as filters of the request log."""

    OK = "2xx"
    RATE_LIMITED = "429"
    REFUSED = "4xx"
    FAILED = "5xx"

    @property
    def label(self) -> str:
        return "Other 4xx" if self is Outcome.REFUSED else self.value

    @property
    def matches(self) -> Q:
        return _OVERVIEW_STATUS[self.value][3]

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
        href = reverse("team", args=[account.team_id]) if account.team_id in team_ids else None
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
    keys: tuple[Key, ...]
    models: tuple[str, ...]
    key: Key | None
    model: str | None


def request_log(
    profile: UserProfile,
    org: Org | None,
    teams: QuerySet[Team],
    *,
    outcome: Outcome | None,
    key_id: int | None,
    model: str | None,
    page_number: str | None,
    open_id: int | None,
) -> RequestLog:
    scope = visible_requests(profile, org, teams)
    team_ids = set(teams.values_list("id", flat=True))
    keys = tuple(
        describe_key(token, profile, team_ids, with_org=org is None)
        for token in Token.objects.filter(id__in=scope.values("token_id"))
        .select_related("user__profile__org", "service_account__team__org")
        .order_by("name", "id")
    )

    models = tuple(router_models())
    key = next((k for k in keys if k.token.id == key_id), None)
    model = model if model in models else None

    filtered = scope
    if key is not None:
        filtered = filtered.filter(token=key.token)
    if model is not None:
        filtered = filtered.filter(model=model)

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
    return RequestLog(page, rows, tabs, keys, models, key, model)
