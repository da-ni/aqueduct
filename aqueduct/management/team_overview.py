from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.db.models import Count, QuerySet

from management.key_activity import (
    WINDOW_DAYS,
    Activity,
    Expiry,
    Window,
    activity_by,
    limit_labels,
    window,
)
from management.models import (
    LimitSet,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserProfile,
)


class Role(StrEnum):
    ADMIN = "Admin"
    ORG_ADMIN = "Organization admin"
    TEAM_ADMIN = "Team admin"
    MEMBER = "Member"

    @classmethod
    def of(cls, profile: UserProfile, membership: TeamMembership) -> "Role":
        if profile.is_admin():
            return cls.ADMIN
        if profile.is_org_admin(membership.team.org):
            return cls.ORG_ADMIN
        return cls.TEAM_ADMIN if membership.is_admin else cls.MEMBER


def _per_minute(limit: int | None) -> str:
    return "no limit" if limit is None else f"{limit:,}"


def _bar_widths(activity: dict[int, Activity]) -> dict[int, int]:
    busiest = max((a.tokens for a in activity.values()), default=0)
    return {key: round(100 * a.tokens / busiest) if busiest else 0 for key, a in activity.items()}


@dataclass(frozen=True)
class TeamRow:
    team: Team
    role: Role | None
    members: int
    service_accounts: int
    limit: str
    activity: Activity
    bar_width: int


def team_rows(
    teams: QuerySet[Team], viewer: UserProfile, query: str, now: datetime
) -> tuple[TeamRow, ...]:
    """The teams page table: one row per team, with service account usage over seven days."""
    found = list(
        # A fresh queryset, because `teams` may come through the viewer's memberships, and
        # that join would count only the viewer as a member.
        Team.objects.filter(id__in=teams.values("id"), name__icontains=query)
        .select_related("org")
        .annotate(
            member_count=Count("member_profiles", distinct=True),
            service_account_count=Count("service_accounts", distinct=True),
        )
        .order_by("name")
    )
    roles = {
        team_id: Role.TEAM_ADMIN if is_admin else Role.MEMBER
        for team_id, is_admin in TeamMembership.objects.filter(user_profile=viewer).values_list(
            "team_id", "is_admin"
        )
    }
    since = now - timedelta(days=WINDOW_DAYS)
    activity = activity_by("token__service_account__team_id", (t.id for t in found), since)
    widths = _bar_widths(activity)
    return tuple(
        TeamRow(
            team=team,
            role=roles.get(team.id),
            members=team.member_count,
            service_accounts=team.service_account_count,
            limit=_per_minute(LimitSet.from_objects(team, team.org).requests_per_minute),
            activity=activity.get(team.id, Activity()),
            bar_width=widths.get(team.id, 0),
        )
        for team in found
    )


@dataclass(frozen=True)
class MemberRow:
    profile: UserProfile
    role: Role
    is_viewer: bool
    owns_service_account: bool
    is_team_admin: bool
    can_change_admin: bool


@dataclass(frozen=True)
class ServiceAccountRow:
    service_account: ServiceAccount
    token: Token | None
    activity: Activity
    expiry: Expiry | None
    bar_width: int


@dataclass(frozen=True)
class TeamPage:
    window: Window
    service_accounts: tuple[ServiceAccountRow, ...]
    members: tuple[MemberRow, ...]
    addable: tuple[UserProfile, ...]
    limits: LimitSet
    limit_labels: tuple[tuple[str, str], ...]
    excluded_models: tuple[str, ...]
    excluded_mcp_servers: tuple[str, ...]


def team_page(team: Team, viewer: UserProfile, can_manage_admins: bool, now: datetime) -> TeamPage:
    """Everything the team page shows. The view decides who may see it and act on it."""
    org = team.org
    accounts = list(team.service_accounts.select_related("token__user").order_by("name"))
    tokens = {sa.id: getattr(sa, "token", None) for sa in accounts}
    since = now - timedelta(days=WINDOW_DAYS)
    activity = activity_by("token_id", (t.id for t in tokens.values() if t is not None), since)
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
    owners = {t.user_id for t in tokens.values() if t is not None}

    memberships = TeamMembership.objects.filter(team=team).select_related(
        "team__org", "user_profile__user"
    )
    members = []
    for membership in sorted(memberships, key=lambda m: m.user_profile.user.email):
        profile = membership.user_profile
        role = Role.of(profile, membership)
        is_viewer = profile.id == viewer.id
        members.append(
            MemberRow(
                profile=profile,
                role=role,
                is_viewer=is_viewer,
                owns_service_account=profile.user_id in owners,
                is_team_admin=membership.is_admin,
                can_change_admin=can_manage_admins
                and role in (Role.TEAM_ADMIN, Role.MEMBER)
                and not is_viewer,
            )
        )

    limits = LimitSet.from_objects(team, org)
    return TeamPage(
        window=window(Request.objects.filter(token__service_account__team=team), now),
        service_accounts=service_accounts,
        members=tuple(members),
        addable=tuple(
            org.user_profiles.exclude(teams=team).select_related("user").order_by("user__email")
        ),
        limits=limits,
        limit_labels=limit_labels(limits),
        excluded_models=tuple(sorted(Token.exclusion_list_from_objects(team, org))),
        excluded_mcp_servers=tuple(
            sorted(Token._mcp_server_exclusion_list_from_objects(team, org))
        ),
    )
