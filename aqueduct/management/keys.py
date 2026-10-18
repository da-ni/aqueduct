from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from django.db.models import Count, F, OuterRef, Subquery, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext, ngettext

from management.access import KeyAct, TeamAct, Viewer
from management.activity import Activity, activity_by, served
from management.charts import Column, Share, chart
from management.formatting import compact, day_label, percent
from management.models import Request, ServiceAccount, Token
from management.periods import Period, Span, week
from management.week_window import Window, window

EXPIRY_WARNING = timedelta(days=7)


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


@dataclass(frozen=True)
class KeyList:
    rows: tuple[KeyRow, ...]
    personal_count: int
    has_service_accounts: bool


def key_list(viewer: Viewer, kind: KeyKind | None, query: str, now: datetime) -> KeyList:
    personal = list(viewer.keys(KeyAct.OWN).order_by("-created_at"))
    service = list(
        ServiceAccount.objects.filter(team__in=viewer.teams(TeamAct.MINE))
        .select_related("team__org", "token")
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
            manageable=viewer.may_key(KeyAct.MANAGE, token),
        )
        for token, entry_kind, team, href in entries
    )
    return KeyList(rows, len(personal), bool(service))


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


def key_report(token: Token, now: datetime, latest: int = 10, top: int = 5) -> KeyReport:
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
