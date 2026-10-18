from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping
from functools import cache
from typing import TYPE_CHECKING, Any, Final, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Q
from django.db.models.fields.mixins import FieldCacheMixin
from django.db.models.functions import Coalesce

if TYPE_CHECKING:
    from management.models import Team, Token, UserProfile

TEAM: Final = "service_account__team"
OWNERS: Final = (TEAM, "user__profile")
RELATED: Final = tuple(f"{path}__org" for path in OWNERS)


def key_org(prefix: str = "") -> Coalesce:
    return Coalesce(*(f"{prefix}{path}" for path in RELATED))


def of_orgs(org_ids: Collection[int], prefix: str = "") -> Q:
    found = Q()
    absent: dict[str, bool] = {}
    for path in OWNERS:
        found |= Q(**absent, **{f"{prefix}{path}__org__in": org_ids})
        absent[f"{prefix}{path.split('__')[0]}__isnull"] = True
    return found


def _hop(obj: models.Model, name: str) -> Any:
    try:
        return getattr(obj, name)
    except ObjectDoesNotExist:
        return None


def _owners(token: "Token") -> "Iterator[Team | UserProfile]":
    for path in OWNERS:
        found: Any = token
        for name in path.split("__"):
            found = _hop(found, name)
            if found is None:
                break
        if found is not None:
            yield found


def owner(token: "Token") -> "Team | UserProfile | None":
    return next(_owners(token), None)


def org_id(token: "Token") -> int | None:
    return next((found.org_id for found in _owners(token) if found.org_id is not None), None)


def loaded(token: "Token") -> bool:
    for path in RELATED:
        found: Any = token
        for name in path.split("__"):
            field = found._meta.get_field(name)
            if isinstance(field, models.ForeignObject) and getattr(found, field.attname) is None:
                found = None
            elif isinstance(field, FieldCacheMixin) and field.is_cached(found):
                found = field.get_cached_value(found)
            else:
                return False
            if found is None:
                break
        else:
            return True
    return True


def personal(user_id: int | None, prefix: str = "") -> Q:
    return Q(**{f"{prefix}user_id": user_id, f"{prefix}service_account__isnull": True})


def is_personal_of(token: "Token", user_id: int | None) -> bool:
    return token.service_account_id is None and token.user_id == user_id


def of_teams(team_ids: "Collection[int] | models.QuerySet[Team]", prefix: str = "") -> Q:
    return Q(**{f"{prefix}{TEAM}__in": team_ids})


def team_of(token: "Token") -> "Team | None":
    return token.service_account.team if token.service_account is not None else None


def _hops() -> Iterator[tuple[type[models.Model], str]]:
    from management.models import Token

    for path in RELATED:
        model: type[models.Model] = Token
        for name in path.split("__"):
            field = model._meta.get_field(name)
            target = cast("type[models.Model]", field.related_model)
            if isinstance(field, models.ForeignObjectRel):
                yield target, field.field.attname
            elif isinstance(field, models.ForeignObject):
                yield model, field.attname
            model = target


@cache
def attribution() -> Mapping[type[models.Model], frozenset[str]]:
    columns: defaultdict[type[models.Model], set[str]] = defaultdict(set)
    for model, column in _hops():
        columns[model].add(column)
    return {model: frozenset(found) for model, found in columns.items()}
