from collections.abc import Mapping

from django.db import models, transaction
from django.db.models import F
from django.db.models.signals import post_delete, post_save, pre_save

from management.models import ServiceAccount, Team, Token, UsageRollupState, UserProfile
from management.models.usage_rollup import HOURLY

ATTRIBUTION: Mapping[type[models.Model], tuple[str, ...]] = {
    Token: ("user_id", "service_account_id"),
    UserProfile: ("org_id",),
    ServiceAccount: ("team_id",),
    Team: ("org_id",),
}


def mark_rollups_dirty() -> None:
    UsageRollupState.objects.filter(name=HOURLY).update(dirty=True, changes=F("changes") + 1)


def _mark_dirty_on_commit(**_: object) -> None:
    transaction.on_commit(mark_rollups_dirty)


def _mark_dirty_on_move(
    sender: type[models.Model], instance: models.Model, **kwargs: object
) -> None:
    if kwargs.get("raw") or instance._state.adding:
        return
    stored = {field: getattr(instance, field) for field in ATTRIBUTION[sender]}
    if sender._default_manager.filter(pk=instance.pk).exclude(**stored).exists():
        _mark_dirty_on_commit()


def _mark_dirty_on_new_profile(
    instance: UserProfile, created: bool, raw: bool = False, **_: object
) -> None:
    if created and not raw and Token.objects.filter(user_id=instance.user_id).exists():
        _mark_dirty_on_commit()


def connect() -> None:
    for sender in ATTRIBUTION:
        pre_save.connect(_mark_dirty_on_move, sender=sender, dispatch_uid=f"rollup-{sender}")
    for sender in (Token, UserProfile):
        post_delete.connect(
            _mark_dirty_on_commit, sender=sender, dispatch_uid=f"rollup-delete-{sender}"
        )
    post_save.connect(
        _mark_dirty_on_new_profile, sender=UserProfile, dispatch_uid="rollup-new-profile"
    )
