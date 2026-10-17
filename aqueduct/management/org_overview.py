from dataclasses import dataclass

from django.conf import settings

from management.models import LimitSet, Org, ServiceAccount, Team, Token


@dataclass(frozen=True)
class SwitchOffRow:
    team: Team | None
    models: tuple[str, ...]
    mcp_servers: tuple[str, ...]


@dataclass(frozen=True)
class OrgPage:
    people: int
    teams: int
    service_accounts: int
    limits: LimitSet
    multipliers: tuple[tuple[str, str], ...]
    max_personal_keys: int
    switch_offs: tuple[SwitchOffRow, ...]


def _multiplier(value: int | None, default: int) -> str:
    return f"{default:,} (default)" if value is None else f"{value:,}"


def org_page(org: Org) -> OrgPage:
    """Everything the organization page shows. The view decides who may see it."""
    teams = list(org.teams.order_by("name"))
    # A blank parent makes Token's rule add the server defaults exactly when the org merges them.
    everyone = SwitchOffRow(
        team=None,
        models=tuple(sorted(Token.exclusion_list_from_objects(org, Org()))),
        mcp_servers=tuple(sorted(Token._mcp_server_exclusion_list_from_objects(org, Org()))),
    )
    team_rows = tuple(
        SwitchOffRow(
            team=team,
            models=tuple(sorted(team.excluded_models)),
            mcp_servers=tuple(sorted(team.excluded_mcp_servers)),
        )
        for team in teams
        if team.excluded_models or team.excluded_mcp_servers
    )
    return OrgPage(
        people=org.user_profiles.count(),
        teams=len(teams),
        service_accounts=ServiceAccount.objects.filter(team__org=org).count(),
        limits=LimitSet.from_objects(org, None),
        multipliers=(
            (
                "Hourly limit, in per-minute limits",
                _multiplier(org.hourly_limit_multiplier, settings.AQUEDUCT_HOURLY_LIMIT_MULTIPLIER),
            ),
            (
                "Daily limit, in per-minute limits",
                _multiplier(org.daily_limit_multiplier, settings.AQUEDUCT_DAILY_LIMIT_MULTIPLIER),
            ),
        ),
        max_personal_keys=settings.MAX_USER_TOKENS,
        switch_offs=(everyone, *team_rows),
    )
