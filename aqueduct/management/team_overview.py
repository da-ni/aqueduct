from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.paginator import Page, Paginator
from django.db.models import Count, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.utils.translation import gettext

from management.activity import Activity, activity_by
from management.charts import TOKEN_SERIES, Stat, Tip, spark
from management.formatting import as_failed, as_requests, as_tokens, compact, day_label, grouped
from management.keys import Expiry, key_activity
from management.models import (
    LimitSet,
    Request,
    Role,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
)
from management.models.exclusions import Exclusion, excluded
from management.periods import week
from management.week_window import Window, window

PAGE_SIZE = 50


def role_of(groups: set[str], membership: TeamMembership) -> Role:
    match UserGroup.of(groups):
        case UserGroup.ADMIN:
            return Role.ADMIN
        case UserGroup.ORG_ADMIN if membership.user_profile.org_id == membership.team.org_id:
            return Role.ORG_ADMIN
        case _:
            return membership.role


def _bar_widths(activity: dict[int, Activity]) -> dict[int, int]:
    busiest = max((a.tokens for a in activity.values()), default=0)
    return {key: round(100 * a.tokens / busiest) if busiest else 0 for key, a in activity.items()}


@dataclass(frozen=True)
class TeamRow:
    team: Team
    is_team_admin: bool
    members: int
    service_accounts: int
    limit: int | None
    activity: Activity
    bar_width: int


def _count_per_team(rows: QuerySet) -> Coalesce:
    return Coalesce(
        Subquery(
            rows.filter(team=OuterRef("pk")).values("team").annotate(n=Count("pk")).values("n")
        ),
        0,
    )


@dataclass(frozen=True)
class TeamList:
    rows: tuple[TeamRow, ...]
    pages: Page


def team_rows(
    teams: QuerySet[Team], admin_of: frozenset[int], query: str, page: str | int, now: datetime
) -> TeamList:
    pages = Paginator(
        Team.objects.filter(id__in=teams.values("id"), name__icontains=query)
        .select_related("org")
        .annotate(
            member_count=_count_per_team(TeamMembership.objects.all()),
            service_account_count=_count_per_team(ServiceAccount.objects.all()),
        )
        .order_by("name", "id"),
        PAGE_SIZE,
    ).get_page(page)
    found = list(pages)
    activity = activity_by("token__service_account__team_id", (t.id for t in found), week(now))
    widths = _bar_widths(activity)
    rows = tuple(
        TeamRow(
            team=team,
            is_team_admin=team.id in admin_of,
            members=team.member_count,
            service_accounts=team.service_account_count,
            limit=LimitSet.from_objects(team, team.org).requests_per_minute,
            activity=activity.get(team.id, Activity()),
            bar_width=widths.get(team.id, 0),
        )
        for team in found
    )
    return TeamList(rows=rows, pages=pages)


@dataclass(frozen=True)
class MemberRow:
    profile: UserProfile
    role: Role
    is_viewer: bool
    owned: tuple[ServiceAccount, ...]
    is_team_admin: bool
    can_change_admin: bool


@dataclass(frozen=True)
class ServiceAccountRow:
    service_account: ServiceAccount
    token: Token | None
    activity: Activity
    expiry: Expiry | None
    bar_width: int

    @property
    def tip(self) -> Tip:
        return Tip(
            gettext("Tokens, 7 days"),
            as_tokens(self.activity.tokens),
            tuple((css, TOKEN_SERIES[css], grouped(n)) for css, n in self.activity.segments),
        )


def team_stats(week_window: Window) -> tuple[Stat, ...]:
    totals = week_window.totals

    def daily(count: Callable[[Activity], int]) -> list[tuple[str, float]]:
        return [(day_label(d.day), count(d.activity)) for d in week_window.days]

    return (
        Stat(
            gettext("Tokens"), compact(totals.tokens), spark(daily(lambda a: a.tokens), as_tokens)
        ),
        Stat(
            gettext("Requests"),
            grouped(totals.requests),
            spark(daily(lambda a: a.requests), as_requests),
        ),
        Stat(gettext("Failed"), totals.failed_share, spark(daily(lambda a: a.failed), as_failed)),
    )


@dataclass(frozen=True)
class TeamPage:
    stats: tuple[Stat, ...]
    service_accounts: tuple[ServiceAccountRow, ...]
    members: tuple[MemberRow, ...]
    member_pages: Page
    member_count: int
    limits: LimitSet
    excluded_models: tuple[str, ...]
    excluded_mcp_servers: tuple[str, ...]


def team_page(
    team: Team,
    viewer: UserProfile,
    can_manage_admins: bool,
    member_query: str,
    member_page: str | int,
    now: datetime,
) -> TeamPage:
    org = team.org
    accounts = list(team.service_accounts.select_related("token__user").order_by("name"))
    tokens = {sa.id: getattr(sa, "token", None) for sa in accounts}
    activity = key_activity((t.id for t in tokens.values() if t is not None), now)
    widths = _bar_widths(activity)
    service_accounts = tuple(
        ServiceAccountRow(
            service_account=sa,
            token=token,
            activity=activity.get(token.id, Activity()) if token else Activity(),
            expiry=Expiry.of(token.expires_at, now) if token else None,
            bar_width=widths.get(token.id, 0) if token else 0,
        )
        for sa, token in ((sa, tokens[sa.id]) for sa in accounts)
    )
    owned = defaultdict(list)
    for sa in accounts:
        if tokens[sa.id] is not None:
            owned[tokens[sa.id].user_id].append(sa)

    all_memberships = TeamMembership.objects.filter(team=team)
    member_pages = Paginator(
        all_memberships.filter(user_profile__user__email__icontains=member_query)
        .select_related("team", "user_profile__user")
        .order_by("-is_admin", "user_profile__user__email", "id"),
        PAGE_SIZE,
    ).get_page(member_page)
    groups = defaultdict(set)
    for user_id, name in (
        get_user_model()
        .groups.through.objects.filter(user_id__in=[m.user_profile.user_id for m in member_pages])
        .values_list("user_id", "group__name")
    ):
        groups[user_id].add(name)
    members = []
    for membership in member_pages:
        profile = membership.user_profile
        role = role_of(groups[profile.user_id], membership)
        is_viewer = profile.id == viewer.id
        members.append(
            MemberRow(
                profile=profile,
                role=role,
                is_viewer=is_viewer,
                owned=tuple(owned[profile.user_id]),
                is_team_admin=membership.is_admin,
                can_change_admin=can_manage_admins
                and role in (Role.TEAM_ADMIN, Role.MEMBER)
                and not is_viewer,
            )
        )

    limits = LimitSet.from_objects(team, org)
    return TeamPage(
        stats=team_stats(window(Request.objects.filter(token__service_account__team=team), now)),
        service_accounts=service_accounts,
        members=tuple(members),
        member_pages=member_pages,
        member_count=all_memberships.count() if member_query else member_pages.paginator.count,
        limits=limits,
        excluded_models=tuple(sorted(excluded(team, Exclusion.MODELS))),
        excluded_mcp_servers=tuple(sorted(excluded(team, Exclusion.MCP_SERVERS))),
    )
