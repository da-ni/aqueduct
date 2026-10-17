from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, F, Max, Q, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from gateway.config import get_router_config
from management.models import Request, ServiceAccount, Token, UserProfile

if TYPE_CHECKING:
    from management.usage_report import Period

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


def as_tokens(count: float) -> str:
    return f"{compact(round(count))} tokens"


def as_cached(count: float) -> str:
    return f"{compact(round(count))} cached tokens"


def as_requests(count: float) -> str:
    return f"{round(count):,} request" + ("" if round(count) == 1 else "s")


def as_failed(count: float) -> str:
    return f"{round(count):,} failed"


TOKEN_SERIES = {"c": "Input from cache", "i": "New input", "o": "Output"}
REQUEST_SERIES = {"i": "Answered", "f": "Failed"}


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
    """The newest request of any kind, so a key used only for files or batches never reads as
    unused. The counts cover only what `served` keeps."""

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


def _sums(since: datetime) -> dict[str, Any]:
    """Totals since `since` of the requests `served` keeps."""
    recent = Q(timestamp__gte=since, model__in=router_models())
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
    activity = activity_by("token_id", (token.id for token, *_ in entries), week(now).start)
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
    since = period.start
    totals = Activity(**requests.aggregate(last_used=Max("timestamp"), **_sums(since)))

    per_day = {
        row.pop("day"): Activity(**row)
        for row in requests.filter(timestamp__gte=since)
        .annotate(day=TruncDate("timestamp"))
        .values("day")
        .annotate(**_sums(since))
        .order_by()
    }
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
    recent = requests.filter(timestamp__gte=week(now).start)
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

    return KeyReport(
        totals=base.totals,
        days=base.days,
        models=models,
        clients=client_text,
        latest=tuple(Period.last(Span.MONTH, now).of(requests).order_by("-timestamp")[:latest]),
    )
