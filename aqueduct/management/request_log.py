from django.db.models import Q, QuerySet

from gateway.config import get_router_config
from management.models import Org, Request, Team, Token, UserProfile


def chosen_org(profile: UserProfile, raw: str | None) -> Org | None:
    """The org whose requests the viewer looks at. `None` means every org, for admins only."""
    if not profile.is_admin():
        return profile.org
    if raw == "all":
        return None
    if raw and raw.isdigit():
        return Org.objects.filter(pk=int(raw)).first() or profile.org
    return profile.org


def visible_tokens(profile: UserProfile, org: Org | None, teams: QuerySet[Team]) -> QuerySet[Token]:
    """Keys whose requests the viewer may see in `org`: every key for an org admin, else their
    own keys and their teams' service accounts. `org=None` means every org."""
    if org is None:
        return Token.objects.all()
    if profile.is_org_admin(org):
        return Token.objects.filter(Q(user__profile__org=org) | Q(service_account__team__org=org))
    return Token.objects.filter(Q(user=profile.user) | Q(service_account__team__in=teams))


def visible_requests(
    profile: UserProfile, org: Org | None, teams: QuerySet[Team]
) -> QuerySet[Request]:
    """Requests the viewer may see in `org`, limited to the models the router serves.

    Besides the requests of `visible_tokens`, a user also sees requests that name their email
    as `user_id` on another key of their org.
    """
    if org is None:
        return Request.objects.filter(model__in=router_models())
    requests = Request.objects.filter(token__in=visible_tokens(profile, org, teams))
    if not profile.is_org_admin(org):
        user = profile.user
        requests |= Request.objects.filter(
            user_id=user.email, token__user__profile__org=profile.org
        )
    return requests.filter(model__in=router_models())


def router_models() -> list[str]:
    return [entry["model_name"] for entry in get_router_config().get("model_list", [])]
