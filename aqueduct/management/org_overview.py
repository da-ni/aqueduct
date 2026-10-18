from dataclasses import dataclass

from django.conf import settings
from django.utils.translation import gettext, ngettext

from management.key_activity import grouped
from management.models import LimitSet, Org, ServiceAccount, Team
from management.models.token import Exclusion, excluded


@dataclass(frozen=True)
class UnavailableRow:
    team: Team | None
    models: tuple[str, ...]
    mcp_servers: tuple[str, ...]


@dataclass(frozen=True)
class OrgPage:
    teams: int
    service_accounts: int
    limits: LimitSet
    multipliers: tuple[tuple[str, str], ...]
    max_personal_keys: int
    unavailable: tuple[UnavailableRow, ...]


def _cap(value: int | None, default: int) -> str:
    if value is None:
        return ngettext(
            "%(count)s min of full rate (default)", "%(count)s min of full rate (default)", default
        ) % {"count": grouped(default)}
    return ngettext("%(count)s min of full rate", "%(count)s min of full rate", value) % {
        "count": grouped(value)
    }


def org_page(org: Org) -> OrgPage:
    teams = list(org.teams.order_by("name"))
    everyone = UnavailableRow(
        team=None,
        models=tuple(sorted(excluded(org, Exclusion.MODELS))),
        mcp_servers=tuple(sorted(excluded(org, Exclusion.MCP_SERVERS))),
    )
    team_rows = tuple(
        UnavailableRow(
            team=team,
            models=tuple(sorted(team.excluded_models)),
            mcp_servers=tuple(sorted(team.excluded_mcp_servers)),
        )
        for team in teams
        if team.excluded_models or team.excluded_mcp_servers
    )
    return OrgPage(
        teams=len(teams),
        service_accounts=ServiceAccount.objects.filter(team__org=org).count(),
        limits=LimitSet.from_objects(org, None),
        multipliers=(
            (
                gettext("Hourly cap"),
                _cap(org.hourly_limit_multiplier, settings.AQUEDUCT_HOURLY_LIMIT_MULTIPLIER),
            ),
            (
                gettext("Daily cap"),
                _cap(org.daily_limit_multiplier, settings.AQUEDUCT_DAILY_LIMIT_MULTIPLIER),
            ),
        ),
        max_personal_keys=settings.MAX_USER_TOKENS,
        unavailable=(everyone, *team_rows),
    )
