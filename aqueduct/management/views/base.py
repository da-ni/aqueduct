from functools import cached_property
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views import View

from management.access import TeamAct, Viewer
from management.models import ServiceAccount, Team, UserProfile
from management.org_choice import viewer_of

NEW_KEY_TAG = "token-regenerated-key"


def show_new_key(request: HttpRequest, secret: str) -> None:
    """Show `secret` once, in the new-key panel at the top of the next page."""
    messages.info(request, secret, extra_tags=NEW_KEY_TAG)


class BaseAqueductView(LoginRequiredMixin, View):
    login_url = reverse_lazy("sso")
    redirect_field_name = "next"

    @property
    def viewer(self) -> Viewer:
        viewer = viewer_of(self.request)
        if viewer is None:
            raise UserProfile.DoesNotExist
        return viewer


class BaseTeamView(BaseAqueductView):
    """A view about the team in the URL; a team outside `viewer.teams(team_act)` is a 404."""

    pk_url_kwarg: str = "id"
    team_act: TeamAct = TeamAct.OPEN

    @cached_property
    def team(self) -> Team:
        return get_object_or_404(
            self.viewer.teams(self.team_act).select_related("org"),
            pk=self.kwargs[self.pk_url_kwarg],
        )

    def get_object(self, queryset: QuerySet[Any] | None = None) -> Team | ServiceAccount:
        return self.team


class BaseServiceAccountView(BaseAqueductView):
    """A view about the service account in the URL; acting on it is managing its team."""

    pk_url_kwarg = "service_account_id"

    @cached_property
    def service_account(self) -> ServiceAccount:
        return get_object_or_404(
            ServiceAccount.objects.filter(
                team__in=self.viewer.teams(TeamAct.MANAGE)
            ).select_related("team__org"),
            pk=self.kwargs[self.pk_url_kwarg],
        )

    @property
    def team(self) -> Team:
        return self.service_account.team

    def get_object(self, queryset: QuerySet[Any] | None = None) -> ServiceAccount:
        return self.service_account
