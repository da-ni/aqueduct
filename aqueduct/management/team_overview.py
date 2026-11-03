from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.contrib.auth import get_user_model
from django.core.paginator import Page, Paginator
from django.utils.translation import gettext, gettext_noop

from management.activity import Activity
from management.charts import TEAM_STATS, TOKEN_SERIES, Stat, Tip, stats, token_widths
from management.formatting import as_tokens, grouped
from management.keys import Expiry, key_activity
from management.models import (
    LimitSet,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
)
from management.week_window import window

PAGE_SIZE = 50


class Role(StrEnum):
    ADMIN = gettext_noop("Platform admin")
    ORG_ADMIN = gettext_noop("Org admin")
    TEAM_ADMIN = gettext_noop("Team admin")
    MEMBER = gettext_noop("Member")

    @property
    def label(self) -> str:
        return gettext(self.value)


def role_of(groups: set[str], membership: TeamMembership) -> Role:
    match UserGroup.of(groups):
        case UserGroup.ADMIN:
            return Role.ADMIN
        case UserGroup.ORG_ADMIN if membership.user_profile.org_id == membership.team.org_id:
            return Role.ORG_ADMIN
        case _:
            return Role.TEAM_ADMIN if membership.is_admin else Role.MEMBER


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


@dataclass(frozen=True)
class TeamPage:
    stats: tuple[Stat, ...]
    service_accounts: tuple[ServiceAccountRow, ...]
    members: tuple[MemberRow, ...]
    member_pages: Page
    member_count: int
    limits: LimitSet


def team_page(
    team: Team,
    viewer: UserProfile,
    can_administer: bool,
    member_query: str,
    member_page: str | int,
    now: datetime,
) -> TeamPage:
    accounts = list(team.service_accounts.select_related("token__user").order_by("name"))
    tokens = {sa.id: getattr(sa, "token", None) for sa in accounts}
    activity = key_activity((t.id for t in tokens.values() if t is not None), now)
    widths = token_widths(activity)
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
                can_change_admin=can_administer
                and role in (Role.TEAM_ADMIN, Role.MEMBER)
                and not is_viewer,
            )
        )

    return TeamPage(
        stats=stats(
            window(Request.objects.filter(token__service_account__team=team), now).points,
            TEAM_STATS,
        ),
        service_accounts=service_accounts,
        members=tuple(members),
        member_pages=member_pages,
        member_count=all_memberships.count() if member_query else member_pages.paginator.count,
        limits=LimitSet.from_objects(team, team.org),
    )
