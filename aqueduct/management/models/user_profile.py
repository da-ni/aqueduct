import logging
from collections.abc import Collection
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.utils.functional import cached_property
from django.utils.translation import gettext, gettext_noop
from django.utils.translation import gettext_lazy as _

from management.models.mixins import LimitMixin, MCPServerExclusionMixin, ModelExclusionMixin
from management.models.org import Org
from management.models.service_account import ServiceAccount
from management.models.team import Team

log = logging.getLogger("aqueduct")


class Role(StrEnum):
    ADMIN = gettext_noop("Platform admin")
    ORG_ADMIN = gettext_noop("Org admin")
    TEAM_ADMIN = gettext_noop("Team admin")
    MEMBER = gettext_noop("Member")

    @property
    def label(self) -> str:
        return gettext(self.value)


class UserGroup(models.TextChoices):
    ADMIN = "admin", _(Role.ADMIN)
    ORG_ADMIN = "org-admin", _(Role.ORG_ADMIN)
    USER = "user", _(Role.MEMBER)

    @classmethod
    def of(cls, names: Collection[str]) -> "UserGroup | None":
        return next(
            (group for group in (cls.ADMIN, cls.ORG_ADMIN, cls.USER) if group in names), None
        )


class UserProfile(LimitMixin, ModelExclusionMixin, MCPServerExclusionMixin, models.Model):
    """
    Holds additional information related to the built-in Django User model.
    Each Django User should have one corresponding UserProfile.
    """

    # Link to the standard Django User model
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,  # Use settings.AUTH_USER_MODEL
        on_delete=models.CASCADE,
        related_name="profile",  # Access profile from user: user.profile
    )

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,  # Keep PROTECT if you don't want to delete Org if profiles exist
        related_name="user_profiles",
    )

    language = models.CharField(
        max_length=8,
        choices=settings.LANGUAGES,
        blank=True,
        db_default="",
        help_text=_("Language of the management pages. Blank follows the browser."),
    )

    teams: "models.ManyToManyField[Team, UserProfile]" = models.ManyToManyField(
        Team, through="TeamMembership", related_name="member_profiles", blank=True
    )

    _saved_org_id: int | None = None

    def __str__(self) -> str:
        return f"{self.user.email} (Profile - {self.org.name})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """An org move ends the memberships in teams of the old org."""
        update_fields = kwargs.get("update_fields")
        writes_org = update_fields is None or not {"org", "org_id"}.isdisjoint(update_fields)
        moved = writes_org and not self._state.adding and self.org_id != self._saved_org_id
        super().save(*args, **kwargs)
        if writes_org:
            self._saved_org_id = self.org_id
        if moved:
            self._leave_other_orgs()

    @classmethod
    def from_db(cls, *args: Any, **kwargs: Any) -> "UserProfile":
        profile = super().from_db(*args, **kwargs)
        profile._saved_org_id = profile.__dict__.get("org_id")
        return profile

    def _leave_other_orgs(self) -> None:
        dropped = self.teammembership_set.exclude(team__org_id=self.org_id)
        email = self.user.email
        for team_id, team, is_admin in dropped.values_list("team_id", "team__name", "is_admin"):
            log.info(
                "Org move of %s ends their membership in team %s (%s), admin: %s.",
                email,
                team_id,
                team,
                is_admin,
            )
        owned = ServiceAccount.objects.filter(
            team__in=dropped.values("team_id"), token__user_id=self.user_id
        )
        for account in owned:
            log.warning(
                "%s left team %s but owns its service account %s (%s); transfer it.",
                email,
                account.team_id,
                account.pk,
                account.name,
            )
        dropped.delete()

    @cached_property
    def _group_names(self) -> frozenset[str]:
        return frozenset(group.name for group in self.user.groups.all())

    def refresh_from_db(self, *args: Any, **kwargs: Any) -> None:
        super().refresh_from_db(*args, **kwargs)
        self.__dict__.pop("_group_names", None)

    @property
    def group(self) -> UserGroup:
        group = UserGroup.of(self._group_names)
        if group is None:
            raise ValidationError("User has no group")
        return group

    @group.setter
    def group(self, group: str) -> None:
        if group not in UserGroup.values:
            raise ValueError(f"Group {group} does not exist!")

        self.__dict__.pop("_group_names", None)
        # Clear existing groups first
        self.user.groups.clear()
        # Add the new group
        try:
            group_obj = Group.objects.get(name=group)
            self.user.groups.add(group_obj)
        except Group.DoesNotExist:
            # Handle case where the group doesn't exist in DB (shouldn't happen with check above)
            raise ObjectDoesNotExist(
                f"The group '{group}' does not exist in the database."
            ) from None

    @property
    def rank(self) -> UserGroup:
        return UserGroup.of(self._group_names) or UserGroup.USER
