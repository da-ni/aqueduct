from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from gateway.config import get_router_config
from management.models import Org, Request, ServiceAccount, Token, UserProfile
from management.models.token import of_org

if TYPE_CHECKING:
    from management.usage_report import Period

EXPIRY_WARNING = timedelta(days=7)
FAILED = Q(status_code__gte=400)
_SUFFIXES, _STEP = ("", "K", "M", "B", "T"), 1000


def compact(count: int) -> str:
    magnitude = float(abs(count))
    for suffix in _SUFFIXES:
        digits = f"{magnitude:.1f}"
        if float(digits) < _STEP or suffix == _SUFFIXES[-1]:
            break
        magnitude /= _STEP
    return ("-" if count < 0 else "") + digits.removesuffix(".0") + suffix


MISSING = "—"
"""What a page shows for a value that does not exist, such as the cache hit rate of no input."""


def percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else MISSING


def as_tokens(count: float) -> str:
    return f"{compact(round(count))} tokens"


def as_cached(count: float) -> str:
    return f"{compact(round(count))} cached tokens"


def as_requests(count: float) -> str:
    return f"{round(count):,} request" + ("" if round(count) == 1 else "s")


def as_failed(count: float) -> str:
    return f"{round(count):,} failed"


TOKEN_SERIES = {"c": "Cached", "n": "New input", "o": "Output"}
REQUEST_SERIES = {"i": "OK", "f": "Failed"}


class KeyKind(StrEnum):
    PERSONAL = "personal"
    SERVICE = "service"

    @property
    def label(self) -> str:
        return "Personal" if self is KeyKind.PERSONAL else "Service accounts"

    @classmethod
    def parse(cls, raw: str) -> "KeyKind | None":
        try:
            return cls(raw)
        except ValueError:
            return None


@dataclass(frozen=True)
class Expiry:
    label: str
    tone: str = "muted"
    at: datetime | None = None

    @classmethod
    def of(cls, expires_at: datetime | None, now: datetime) -> "Expiry":
        if expires_at is None:
            return cls("No expiry")
        if expires_at <= now:
            return cls("Expired", "st-err font-semibold", expires_at)
        if expires_at - now <= EXPIRY_WARNING:
            days = (expires_at - now).days
            label = "Today" if days == 0 else f"In {days} day{'s' if days > 1 else ''}"
            return cls(label, "st-warn font-semibold", expires_at)
        return cls(f"{timezone.localtime(expires_at):%-d %b %Y}", at=expires_at)


@dataclass(frozen=True)
class Activity:
    requests: int = 0
    failed: int = 0
    input: int = 0
    cached: int = 0
    output: int = 0
    last_used: datetime | None = None
    """The newest request of any kind, so a key used only for files or batches never reads as
    unused. Only per-key activity (`key_activity`, `key_report`) fills it in; it stays empty on
    team totals. The counts cover only what `served` keeps."""

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
        """Cached input, new input and output, as CSS class and token count."""
        return (("c", self.cached), ("n", self.input - self.cached), ("o", self.output))


def week(now: datetime) -> "Period":
    """The "7 days" every page shows: today and the six days before, as on the Overview."""
    from management.usage_report import Period, Span  # usage_report imports this module

    return Period.last(Span.WEEK, now)


def router_models() -> list[str]:
    return [entry["model_name"] for entry in get_router_config().get("model_list", [])]


def served(requests: QuerySet[Request]) -> QuerySet[Request]:
    """The requests every page counts and lists: those for a model the router serves. Calls
    that name no model, such as listing models or files, are left out."""
    return requests.filter(model__in=router_models())


LISTED_KEYS = 500


def of_keys(requests: QuerySet[Request], tokens: QuerySet[Token]) -> QuerySet[Request]:
    """The `requests` of the keys of `tokens`. Up to `LISTED_KEYS` keys are named by id, so the
    planner sees how few requests they have and reads them through the token index, instead of
    scanning every org's requests by model or time. More keys stay a subquery."""
    ids = list(tokens.order_by().values_list("id", flat=True)[: LISTED_KEYS + 1])
    if len(ids) > LISTED_KEYS:
        return requests.filter(token__in=tokens)
    return requests.filter(token_id__in=ids)


_SUMS = {
    "requests": Count("id"),
    "failed": Count("id", filter=FAILED),
    "input": Sum("input_tokens", default=0),
    "cached": Sum("cached_input_tokens", default=0),
    "output": Sum("output_tokens", default=0),
}
"""The `Activity` counts, for requests already narrowed to `served` ones in the `week`."""


def activity_by(field: str, ids: Iterable[int], period: "Period") -> dict[int, Activity]:
    """Activity in `period` grouped by `field`, a lookup that names a token or a team id.
    Leaves `last_used` empty; `key_activity` fills it in."""
    rows = (
        served(period.of(Request.objects.filter(**{f"{field}__in": list(ids)})))
        .values(field)
        .annotate(**_SUMS)
        .order_by()
    )
    return {row.pop(field): Activity(**row) for row in rows}


def key_activity(token_ids: Iterable[int], now: datetime) -> dict[int, Activity]:
    ids = list(token_ids)
    activity = activity_by("token_id", ids, week(now))
    # One probe of the (token, timestamp) index per key, however much history it has.
    newest = Request.objects.filter(token=OuterRef("pk")).order_by("-timestamp")
    used = Token.objects.filter(id__in=ids).annotate(used=Subquery(newest.values("timestamp")[:1]))
    return {
        token_id: replace(activity.get(token_id, Activity()), last_used=at)
        for token_id, at in used.values_list("id", "used")
    }


@dataclass(frozen=True)
class KeyRow:
    token: Token
    kind: KeyKind
    team: str | None
    href: str
    activity: Activity
    expiry: Expiry
    bar_width: int
    manageable: bool
    """Whether the viewer may rotate, edit and delete the key: their own personal key, or a
    service account of a team they administer."""


def personal_tokens(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Token]:
    if user.pk is None:
        return Token.objects.none()
    return Token.objects.filter(user_id=user.pk, service_account__isnull=True)


@dataclass(frozen=True)
class KeyAccess:
    """Whose key pages a viewer may open: an admin every key, an org admin every key of their
    org, anyone else their personal keys and their teams' service accounts. Opening a key shows
    its usage; changing it takes ownership or team admin rights, as the key's views check."""

    user_id: int
    every_org: bool
    org: Org | None
    """The org whose every key an org admin may open."""
    team_ids: frozenset[int]

    @classmethod
    def of(cls, profile: UserProfile) -> "KeyAccess":
        org = profile.org
        return cls(
            user_id=profile.user_id,
            every_org=profile.is_admin(),
            org=org if org is not None and profile.is_org_admin(org) else None,
            team_ids=frozenset(profile.teams.values_list("id", flat=True)),
        )

    def tokens(self) -> QuerySet[Token]:
        if self.every_org:
            return Token.objects.all()
        found = Q(user_id=self.user_id, service_account__isnull=True) | Q(
            service_account__team_id__in=self.team_ids
        )
        return Token.objects.filter(found | of_org(self.org) if self.org else found)

    def can_open(self, token: Token) -> bool:
        """`token` in `tokens()`, without a query when its service account's team, or for a
        personal key its owner's profile, is already loaded."""
        if self.every_org:
            return True
        account = token.service_account
        if account is not None:
            return account.team_id in self.team_ids or (
                self.org is not None and account.team.org_id == self.org.id
            )
        if token.user_id == self.user_id:
            return True
        owner = getattr(token.user, "profile", None)
        return self.org is not None and owner is not None and owner.org_id == self.org.id


@dataclass(frozen=True)
class KeyList:
    rows: tuple[KeyRow, ...]
    personal_count: int
    has_service_accounts: bool


def key_list(profile: UserProfile, kind: KeyKind | None, query: str, now: datetime) -> KeyList:
    personal = list(personal_tokens(profile.user).order_by("-created_at"))
    service = list(
        ServiceAccount.objects.filter(team__in=profile.teams.all())
        .select_related("team__org", "token")
        .order_by("team__name", "name")
    )
    administered_teams = {
        team.id for team in {sa.team for sa in service} if profile.is_team_admin(team)
    }
    administered = {sa.id for sa in service if sa.team_id in administered_teams}
    entries: list[tuple[Token, KeyKind, str | None, str]] = [
        (token, KeyKind.PERSONAL, None, reverse("token_detail", kwargs={"id": token.id}))
        for token in personal
    ] + [
        (
            sa.token,
            KeyKind.SERVICE,
            sa.team.name,
            reverse("token_detail", kwargs={"id": sa.token.id}),
        )
        for sa in service
        if hasattr(sa, "token")
    ]
    needle = query.casefold()
    entries = [
        (token, entry_kind, team, href)
        for token, entry_kind, team, href in entries
        if (kind is None or entry_kind is kind)
        and (needle in token.name.casefold() or needle in (team or "").casefold())
    ]
    activity = key_activity((token.id for token, *_ in entries), now)
    busiest = max((a.tokens for a in activity.values()), default=0)
    rows = tuple(
        KeyRow(
            token=token,
            kind=entry_kind,
            team=team,
            href=href,
            activity=(found := activity.get(token.id, Activity())),
            expiry=Expiry.of(token.expires_at, now),
            bar_width=round(100 * found.tokens / busiest) if busiest else 0,
            manageable=entry_kind is KeyKind.PERSONAL or token.service_account_id in administered,
        )
        for token, entry_kind, team, href in entries
    )
    return KeyList(rows, len(personal), bool(service))


@dataclass(frozen=True)
class Day:
    day: date
    activity: Activity


@dataclass(frozen=True)
class Tip:
    """What a chart mark shows on hover and tells a screen reader."""

    heading: str
    value: str
    parts: tuple[tuple[str, str, str], ...] = ()
    """Swatch CSS class, series name and value of each stacked segment."""

    def __str__(self) -> str:
        detail = ", ".join(f"{name} {value}" for _, name, value in self.parts)
        return f"{self.heading}: {self.value}" + (f" ({detail})" if detail else "")


@dataclass(frozen=True)
class SparkBar:
    height: int
    tip: Tip


def spark(
    points: Sequence[tuple[str, float]], show: Callable[[float], str]
) -> tuple[SparkBar, ...]:
    """One bar per labelled value, its height in percent of the largest."""
    return tuple(
        SparkBar(height, Tip(label, show(value)))
        for (label, value), height in zip(points, heights([v for _, v in points]), strict=True)
    )


@dataclass(frozen=True)
class Stat:
    label: str
    value: str
    spark: tuple[SparkBar, ...]


@dataclass(frozen=True)
class Column:
    label: str
    tip: Tip
    segments: tuple[tuple[str, int], ...]
    short: str = ""
    """The axis label below the `sm` breakpoint, when it differs from `label`."""

    @property
    def height(self) -> int:
        return sum(height for _, height in self.segments)


@dataclass(frozen=True)
class Share:
    name: str
    value: str
    width: int


def heights(values: Sequence[float]) -> tuple[int, ...]:
    top = max(values, default=0)
    return tuple(round(100 * v / top) if top else 0 for v in values)


@dataclass(frozen=True)
class Window:
    """Totals over the `week` and one entry per day."""

    totals: Activity
    days: tuple[Day, ...]

    @property
    def stats(self) -> tuple[Stat, ...]:
        totals = self.totals

        def daily(field: str) -> list[tuple[str, float]]:
            return [(f"{d.day:%-d %b}", getattr(d.activity, field)) for d in self.days]

        return (
            Stat("Input tokens", compact(totals.input), spark(daily("input"), as_tokens)),
            Stat("Output tokens", compact(totals.output), spark(daily("output"), as_tokens)),
            Stat("Cache hits", totals.cache_hits, spark(daily("cached"), as_cached)),
            Stat("Requests", f"{totals.requests:,}", spark(daily("requests"), as_requests)),
            Stat("Failed", totals.failed_share, spark(daily("failed"), as_failed)),
        )


@dataclass(frozen=True)
class KeyReport(Window):
    models: tuple[Share, ...]
    clients: str
    latest: tuple[Request, ...]

    def chart(self, metric: str) -> tuple[Column, ...]:
        return chart([(f"{d.day:%-d %b}", d.activity) for d in self.days], metric)


def chart(
    points: Sequence[tuple[str, Activity]], metric: str, every: int = 1
) -> tuple[Column, ...]:
    """One column per point, its segments as CSS class and percent of the busiest point.

    Only every `every`-th column keeps its axis label, so dense charts stay readable."""
    values: list[tuple[tuple[str, int], ...]]
    if metric == "requests":
        names, show = REQUEST_SERIES, as_requests
        values = [(("i", a.requests - a.failed), ("f", a.failed)) for _, a in points]
    else:
        names, show = TOKEN_SERIES, as_tokens
        values = [a.segments for _, a in points]
    top = max((sum(n for _, n in segs) for segs in values), default=0)

    def tip(label: str, segs: tuple[tuple[str, int], ...]) -> Tip:
        total = sum(n for _, n in segs)
        parts = tuple((css, names[css], f"{n:,}") for css, n in segs) if total else ()
        return Tip(label, show(total), parts)

    return tuple(
        Column(
            label if index % every == 0 else "",
            tip(label, segs),
            tuple((css, round(100 * n / top)) for css, n in segs if n),
        )
        for index, ((label, _), segs) in enumerate(zip(points, values, strict=True))
    )


def window(requests: QuerySet[Request], now: datetime) -> Window:
    period = week(now)
    per_day = {
        row.pop("day"): Activity(**row)
        for row in served(period.of(requests))
        .annotate(day=TruncDate("timestamp"))
        .values("day")
        .annotate(**_SUMS)
        .order_by()
    }
    totals = Activity(
        **{name: sum(getattr(day, name) for day in per_day.values()) for name in _SUMS}
    )
    days = tuple(
        Day(day, per_day.get(day, Activity()))
        for day in (timezone.localdate(bucket) for bucket in period.buckets)
    )
    return Window(totals, days)


def key_report(token: Token, now: datetime, latest: int = 10, top: int = 5) -> KeyReport:
    """`latest` covers the 30 days the request log opens on, so each row's link shows it."""
    from management.usage_report import Period, Span  # usage_report imports this module

    every = Request.objects.filter(token=token)
    requests = served(every)
    recent = week(now).of(requests)
    base = window(every, now)

    by_model = list(
        recent.values("model")
        .annotate(tokens=Sum(F("input_tokens") + F("output_tokens")))
        .order_by("-tokens", "model")[:top]
    )
    most = max((row["tokens"] for row in by_model), default=0)
    models = tuple(
        Share(
            row["model"], compact(row["tokens"]), round(100 * row["tokens"] / most) if most else 0
        )
        for row in by_model
    )

    clients = [
        (row["user_agent"].split(" ")[0] or "Unknown", row["n"])
        for row in recent.values("user_agent").annotate(n=Count("id")).order_by("-n")[:3]
    ]
    client_text = ", ".join(f"{name} {percent(n, base.totals.requests)}" for name, n in clients)

    newest = every.order_by("-timestamp").values_list("timestamp", flat=True).first()
    return KeyReport(
        totals=replace(base.totals, last_used=newest),
        days=base.days,
        models=models,
        clients=client_text,
        latest=tuple(Period.last(Span.MONTH, now).of(requests).order_by("-timestamp")[:latest]),
    )
