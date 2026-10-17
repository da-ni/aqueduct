from collections.abc import Mapping

from django.db import models
from django.db.models.signals import post_delete, pre_save

from management.models import ServiceAccount, Team, Token, UsageRollupState, UserProfile
from management.models.usage_rollup import HOURLY

ATTRIBUTION: Mapping[type[models.Model], tuple[str, ...]] = {
    Token: ("user_id", "service_account_id"),
    UserProfile: ("org_id",),
    ServiceAccount: ("team_id",),
    Team: ("org_id",),
}
"""The fields that decide which org a key's requests count for in the usage rollups. Changing
one, or deleting a key or a profile, marks the rollups dirty. `QuerySet.update()` bypasses
this. No receiver listens to `Request`, so deleting a key still deletes its requests in bulk."""


def mark_rollups_dirty(**_: object) -> None:
    UsageRollupState.objects.filter(name=HOURLY, dirty=False).update(dirty=True)


def _mark_dirty_on_move(
    sender: type[models.Model], instance: models.Model, **kwargs: object
) -> None:
    if kwargs.get("raw") or instance._state.adding:
        return
    stored = {field: getattr(instance, field) for field in ATTRIBUTION[sender]}
    if sender._default_manager.filter(pk=instance.pk).exclude(**stored).exists():
        mark_rollups_dirty()


def connect() -> None:
    for sender in ATTRIBUTION:
        pre_save.connect(_mark_dirty_on_move, sender=sender, dispatch_uid=f"rollup-{sender}")
    for sender in (Token, UserProfile):
        post_delete.connect(
            mark_rollups_dirty, sender=sender, dispatch_uid=f"rollup-delete-{sender}"
        )
