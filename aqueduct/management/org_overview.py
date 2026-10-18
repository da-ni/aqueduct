from dataclasses import dataclass

from django.conf import settings
from django.utils.formats import number_format
from django.utils.translation import gettext, ngettext

from management.models import LimitSet, Org, ServiceAccount, Team, Token


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
    """A multiplier as the minutes at the full per-minute rate that the window allows."""
    if value is None:
        return ngettext(
            "%(count)s min of full rate (default)", "%(count)s min of full rate (default)", default
        ) % {"count": number_format(default, force_grouping=True)}
    return ngettext("%(count)s min of full rate", "%(count)s min of full rate", value) % {
        "count": number_format(value, force_grouping=True)
    }


def org_page(org: Org) -> OrgPage:
    """Everything the organization page shows. The view decides who may see it."""
    teams = list(org.teams.order_by("name"))
    # A blank parent makes Token's rule add the server defaults exactly when the org merges them.
    everyone = UnavailableRow(
        team=None,
        models=tuple(sorted(Token.exclusion_list_from_objects(org, Org()))),
        mcp_servers=tuple(sorted(Token._mcp_server_exclusion_list_from_objects(org, Org()))),
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
