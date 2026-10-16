from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, F, Max, Q, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from management.models import LimitSet, Request, ServiceAccount, Token, UserProfile

WINDOW_DAYS = 7
EXPIRY_WARNING = timedelta(days=7)
FAILED = Q(status_code__gte=400)
_MILLION, _THOUSAND = 1_000_000, 1_000


def compact(count: int) -> str:
    if count >= _MILLION:
        return f"{count / _MILLION:.1f}".removesuffix(".0") + "M"
    if count >= _THOUSAND:
        return f"{count / _THOUSAND:.1f}".removesuffix(".0") + "K"
    return str(count)


def percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "n/a"


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

    @classmethod
    def of(cls, expires_at: datetime | None, now: datetime) -> "Expiry":
        if expires_at is None:
            return cls("Never")
        if expires_at <= now:
            return cls("Expired", "st-err font-semibold")
        if expires_at - now <= EXPIRY_WARNING:
            days = (expires_at - now).days
            label = "Today" if days == 0 else f"In {days} day{'s' if days > 1 else ''}"
            return cls(label, "st-warn font-semibold")
        return cls(f"{timezone.localtime(expires_at):%-d %b %Y}")


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
        """Cached input, new input and output, as CSS class and token count."""
        return (("c", self.cached), ("i", self.input - self.cached), ("o", self.output))


def _sums(since: datetime) -> dict[str, Any]:
    recent = Q(timestamp__gte=since)
    return {
        "requests": Count("id", filter=recent),
        "failed": Count("id", filter=recent & FAILED),
        "input": Sum("input_tokens", filter=recent, default=0),
        "cached": Sum("cached_input_tokens", filter=recent, default=0),
        "output": Sum("output_tokens", filter=recent, default=0),
    }


def activity_by(field: str, ids: Iterable[int], since: datetime) -> dict[int, Activity]:
    """Request activity grouped by `field`, a lookup that names a token or a team id."""
    rows = (
        Request.objects.filter(**{f"{field}__in": list(ids)})
        .values(field)
        .annotate(last_used=Max("timestamp"), **_sums(since))
    )
    return {row.pop(field): Activity(**row) for row in rows}


@dataclass(frozen=True)
class KeyRow:
    token: Token
    kind: KeyKind
    team: str | None
    href: str
    activity: Activity
    expiry: Expiry
    bar_width: int

    @property
    def manageable(self) -> bool:
        return self.kind is KeyKind.PERSONAL


def personal_tokens(user: AbstractBaseUser | AnonymousUser) -> QuerySet[Token]:
    return Token.objects.filter(user=user, service_account__isnull=True)


@dataclass(frozen=True)
class KeyList:
    rows: tuple[KeyRow, ...]
    personal_count: int
    has_service_accounts: bool


def key_list(profile: UserProfile, kind: KeyKind | None, query: str, now: datetime) -> KeyList:
    personal = list(personal_tokens(profile.user).order_by("-created_at"))
    service = list(
        ServiceAccount.objects.filter(team__in=profile.teams.all())
        .select_related("team", "token")
        .order_by("team__name", "name")
    )
    entries: list[tuple[Token, KeyKind, str | None, str]] = [
        (token, KeyKind.PERSONAL, None, reverse("token_detail", kwargs={"id": token.id}))
        for token in personal
    ] + [
        (sa.token, KeyKind.SERVICE, sa.team.name, reverse("team", kwargs={"id": sa.team_id}))
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
    since = now - timedelta(days=WINDOW_DAYS)
    activity = activity_by("token_id", (token.id for token, *_ in entries), since)
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
        )
        for token, entry_kind, team, href in entries
    )
    return KeyList(rows, len(personal), bool(service))


@dataclass(frozen=True)
class Day:
    day: date
    activity: Activity


@dataclass(frozen=True)
class Stat:
    label: str
    value: str
    spark: tuple[int, ...]


@dataclass(frozen=True)
class Column:
    day: date
    title: str
    segments: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Share:
    name: str
    value: str
    width: int


def _heights(values: Sequence[int]) -> tuple[int, ...]:
    top = max(values, default=0)
    return tuple(round(100 * v / top) if top else 0 for v in values)


@dataclass(frozen=True)
class Window:
    """Totals over the last WINDOW_DAYS days and one entry per day."""

    totals: Activity
    days: tuple[Day, ...]

    @property
    def stats(self) -> tuple[Stat, ...]:
        days = [d.activity for d in self.days]
        totals = self.totals
        return (
            Stat("Input tokens", compact(totals.input), _heights([a.input for a in days])),
            Stat("Output tokens", compact(totals.output), _heights([a.output for a in days])),
            Stat("Cache hits", totals.cache_hits, _heights([a.cached for a in days])),
            Stat("Requests", f"{totals.requests:,}", _heights([a.requests for a in days])),
            Stat("Failed", totals.failed_share, _heights([a.failed for a in days])),
        )


@dataclass(frozen=True)
class KeyReport(Window):
    models: tuple[Share, ...]
    clients: str
    latest: tuple[Request, ...]

    def chart(self, metric: str) -> tuple[Column, ...]:
        """One column per day, its segments as CSS class and percent of the busiest day."""
        if metric == "requests":
            values = [
                (("i", d.activity.requests - d.activity.failed), ("f", d.activity.failed))
                for d in self.days
            ]
            titles = [f"{d.activity.requests:,} requests" for d in self.days]
        else:
            values = [d.activity.segments for d in self.days]
            titles = [f"{compact(d.activity.tokens)} tokens" for d in self.days]
        top = max((sum(n for _, n in segs) for segs in values), default=0)
        return tuple(
            Column(d.day, title, tuple((css, round(100 * n / top)) for css, n in segs if n))
            for d, title, segs in zip(self.days, titles, values, strict=True)
        )


def window(requests: QuerySet[Request], now: datetime) -> Window:
    since = now - timedelta(days=WINDOW_DAYS)
    totals = Activity(**requests.aggregate(last_used=Max("timestamp"), **_sums(since)))

    per_day = {
        row.pop("day"): Activity(**row)
        for row in requests.filter(timestamp__gte=since)
        .annotate(day=TruncDate("timestamp"))
        .values("day")
        .annotate(**_sums(since))
        .order_by()
    }
    today = timezone.localdate(now)
    days = tuple(
        Day(day, per_day.get(day, Activity()))
        for day in (today - timedelta(days=n) for n in reversed(range(WINDOW_DAYS)))
    )
    return Window(totals, days)


def key_report(token: Token, now: datetime, latest: int = 10, top: int = 5) -> KeyReport:
    since = now - timedelta(days=WINDOW_DAYS)
    requests = Request.objects.filter(token=token)
    recent = requests.filter(timestamp__gte=since)
    base = window(requests, now)

    by_model = list(
        recent.exclude(model="")
        .values("model")
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

    return KeyReport(
        totals=base.totals,
        days=base.days,
        models=models,
        clients=client_text,
        latest=tuple(requests.order_by("-timestamp")[:latest]),
    )


def limit_labels(limits: LimitSet) -> tuple[tuple[str, str], ...]:
    return tuple(
        (label, "no limit" if value is None else f"{value:,}")
        for label, value in (
            ("Requests per minute", limits.requests_per_minute),
            ("Input tokens per minute", limits.input_tokens_per_minute),
            ("Output tokens per minute", limits.output_tokens_per_minute),
        )
    )
