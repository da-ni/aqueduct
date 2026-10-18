from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format, get_format, number_format
from django.utils.functional import Promise
from django.utils.translation import gettext, ngettext, pgettext, pgettext_lazy
from django.utils.translation import gettext_lazy as _

from gateway.config import model_names
from management.models import Org, Request, ServiceAccount, Token, UserProfile
from management.models.token import of_org

if TYPE_CHECKING:
    from management.usage_report import Period

EXPIRY_WARNING = timedelta(days=7)
FAILED = Q(status_code__gte=400)
_STEP = 1000
_SCALES = (
    pgettext_lazy("compact number", "%(number)sK"),
    pgettext_lazy("compact number", "%(number)sM"),
    pgettext_lazy("compact number", "%(number)sB"),
    pgettext_lazy("compact number", "%(number)sT"),
)


def grouped(count: int) -> str:
    return number_format(count, force_grouping=True)


def decimal(value: float, places: int) -> str:
    return number_format(Decimal(f"{value:.{places}f}"))


def compact(count: int) -> str:
    magnitude, scale = float(abs(count)), None
    for next_scale in _SCALES:
        if float(f"{magnitude:.1f}") < _STEP:
            break
        magnitude, scale = magnitude / _STEP, next_scale
    digits = decimal(magnitude, 1).removesuffix(f"{get_format('DECIMAL_SEPARATOR')}0")
    number = ("-" if count < 0 else "") + digits
    return number if scale is None else scale % {"number": number}


MISSING = "—"


def as_percent(number: float) -> str:
    return pgettext("percent", "%(number)s%%") % {"number": grouped(round(number))}


def percent(part: int, whole: int) -> str:
    return as_percent(100 * part / whole) if whole else MISSING


def as_tokens(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s token", "%(count)s tokens", n) % {"count": compact(n)}


def as_cached(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s cached token", "%(count)s cached tokens", n) % {"count": compact(n)}


def as_requests(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s request", "%(count)s requests", n) % {"count": grouped(n)}


def as_failed(count: float) -> str:
    n = round(count)
    return ngettext("%(count)s failed", "%(count)s failed", n) % {"count": grouped(n)}


def day_label(day: date) -> str:
    return date_format(day, "DAY_MONTH_FORMAT")


TOKEN_SERIES = {"c": _("Cached"), "n": _("New input"), "o": _("Output")}
REQUEST_SERIES = {"i": _("OK"), "f": _("Failed")}


class KeyKind(StrEnum):
    PERSONAL = "personal"
    SERVICE = "service"

    @property
    def label(self) -> str:
        return gettext("Personal") if self is KeyKind.PERSONAL else gettext("Service accounts")

    @classmethod
    def parse(cls, raw: str) -> "KeyKind | None":
        try:
            return cls(raw)
        except ValueError:
            return None


@dataclass(frozen=True)
class Expiry:
    label: str
    sentence: str
    tone: str = "muted"
    at: datetime | None = None

    @classmethod
    def of(cls, expires_at: datetime | None, now: datetime) -> "Expiry":
        if expires_at is None:
            return cls(gettext("No expiry"), gettext("Never expires"))
        if expires_at <= now:
            return cls(gettext("Expired"), gettext("Expired"), "st-err font-semibold", expires_at)
        if expires_at - now <= EXPIRY_WARNING:
            days = (timezone.localdate(expires_at) - timezone.localdate(now)).days
            if days == 0:
                label, sentence = gettext("Today"), gettext("Expires today")
            else:
                label = ngettext("In %(days)s day", "In %(days)s days", days) % {"days": days}
                sentence = ngettext("Expires in %(days)s day", "Expires in %(days)s days", days) % {
                    "days": days
                }
            return cls(label, sentence, "st-warn font-semibold", expires_at)
        day = date_format(timezone.localtime(expires_at), "DAY_FORMAT")
        return cls(day, gettext("Expires %(date)s") % {"date": day}, at=expires_at)


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


def week(now: datetime) -> "Period":
    from management.usage_report import Period, Span

    return Period.last(Span.WEEK, now)


def served(requests: QuerySet[Request]) -> QuerySet[Request]:
    return requests.filter(model__in=model_names())


LISTED_KEYS = 500


def of_keys(requests: QuerySet[Request], tokens: QuerySet[Token]) -> QuerySet[Request]:
    """Postgres estimates rows for a literal id list but not for a subquery, so up to
    `LISTED_KEYS` ids keep the plan on the (token, timestamp) index."""
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


def activity_by(field: str, ids: Iterable[int], period: "Period") -> dict[int, Activity]:
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


def personal_tokens(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Token]:
    if user.pk is None:
        return Token.objects.none()
    return Token.objects.filter(user_id=user.pk, service_account__isnull=True)


@dataclass(frozen=True)
class KeyAccess:
    user_id: int
    every_org: bool
    org: Org | None
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
    heading: str
    value: str
    parts: tuple[tuple[str, str | Promise, str], ...] = ()

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
    totals: Activity
    days: tuple[Day, ...]

    @property
    def stats(self) -> tuple[Stat, ...]:
        totals = self.totals

        def daily(field: str) -> list[tuple[str, float]]:
            return [(day_label(d.day), getattr(d.activity, field)) for d in self.days]

        return (
            Stat(gettext("Input tokens"), compact(totals.input), spark(daily("input"), as_tokens)),
            Stat(
                gettext("Output tokens"), compact(totals.output), spark(daily("output"), as_tokens)
            ),
            Stat(gettext("Cache hits"), totals.cache_hits, spark(daily("cached"), as_cached)),
            Stat(
                gettext("Requests"), grouped(totals.requests), spark(daily("requests"), as_requests)
            ),
            Stat(gettext("Failed"), totals.failed_share, spark(daily("failed"), as_failed)),
        )


@dataclass(frozen=True)
class KeyReport(Window):
    models: tuple[Share, ...]
    clients: str
    latest: tuple[Request, ...]

    def chart(self, metric: str) -> tuple[Column, ...]:
        found = chart([(day_label(d.day), d.activity) for d in self.days], metric)
        return tuple(
            replace(column, short=date_format(d.day, "AXIS_DAY_FORMAT"))
            for column, d in zip(found, self.days, strict=True)
        )


def chart(
    points: Sequence[tuple[str, Activity]], metric: str, every: int = 1
) -> tuple[Column, ...]:
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
        parts = tuple((css, names[css], grouped(n)) for css, n in segs) if total else ()
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
    from management.usage_report import Period, Span

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
