import dataclasses
from collections.abc import Mapping
from typing import ClassVar

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.defaultfilters import floatformat
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.utils.translation import gettext, gettext_lazy, ngettext_lazy
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from gateway.config import model_names
from gateway.rate_limiting import get_per_token_usage
from management.access import KeyAct, TeamAct
from management.availability import Reach, configured_mcp_servers
from management.deletion import deletion
from management.forms import TokenCreateForm
from management.key_activity import Expiry, KeyKind, key_list, key_report, personal_tokens
from management.models import LimitSet, Token
from management.org_choice import EVERY_ORG
from management.ownership import RELATED, of_teams, org_id, team_of
from management.request_log import Key, Row
from management.views.base import ONLY_TEAM_ADMINS, BaseAqueductView, show_new_key


class UserTokensView(BaseAqueductView, TemplateView):
    template_name = "management/tokens.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        kind = KeyKind.parse(self.request.GET.get("kind", ""))
        query = self.request.GET.get("q", "").strip()
        max_tokens = getattr(settings, "MAX_USER_TOKENS", 3)
        keys = key_list(self.viewer, kind, query, timezone.now())
        context.update(
            keys=keys,
            kinds=tuple(KeyKind),
            kind=kind,
            query=query,
            max_tokens=max_tokens,
            can_add_token=keys.personal_count < max_tokens,
        )
        return context


REQUEST_WINDOWS = {
    "hour": (
        gettext_lazy("Hourly limit"),
        ngettext_lazy(
            "%(used)s / %(limit)s request this hour",
            "%(used)s / %(limit)s requests this hour",
            "limit",
        ),
    ),
    "day": (
        gettext_lazy("Daily limit"),
        ngettext_lazy(
            "%(used)s / %(limit)s request today", "%(used)s / %(limit)s requests today", "limit"
        ),
    ),
}


FULL_PERCENT = 100
NEARLY_FULL_PERCENT = 80


@dataclasses.dataclass(frozen=True)
class RateWindow:
    """Requests a key used in one window, against that window's request limit."""

    label: str
    title: str
    used: str
    limit: int | None
    percent: int

    @property
    def tone(self) -> str:
        if self.percent >= FULL_PERCENT:
            return "progress-error"
        return "progress-warning" if self.percent >= NEARLY_FULL_PERCENT else "progress-primary"


def rate_windows(
    limits: LimitSet, usage: Mapping[str, Mapping[str, float]]
) -> tuple[RateWindow, ...]:
    """Request use against the hourly and daily request limits; none without a request limit.

    `usage` is a key's entry from `gateway.rate_limiting.get_per_token_usage`.
    """
    if limits.requests_per_minute is None:
        return ()
    found = []
    for name, _seconds, requests, _input, _output in limits.windows():
        if name not in REQUEST_WINDOWS:
            continue
        label, title = REQUEST_WINDOWS[name]
        used = usage[name]["req"]
        shown = floatformat(used, "0")
        percent = min(FULL_PERCENT, round(used * 100 / requests)) if requests else 0
        found.append(
            RateWindow(
                str(label), title % {"used": shown, "limit": requests}, shown, requests, percent
            )
        )
    return tuple(found)


class TokenDetailView(BaseAqueductView, DetailView):
    model = Token
    template_name = "management/token_detail.html"
    pk_url_kwarg = "id"
    context_object_name = "token"

    def get_queryset(self) -> QuerySet:
        return self.viewer.keys(KeyAct.OPEN).select_related(*RELATED)

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        viewer, token = self.viewer, self.object
        now = timezone.now()
        report = key_report(token, now)
        limits = token.get_limit()
        log_query: dict[str, object] = {"key": token.id, "span": "1m"}
        if not viewer.may_key(KeyAct.SEE, token):
            log_query["org"] = org_id(token) or EVERY_ORG
        context.update(
            report=report,
            latest=tuple(Row(request, Key(token, None, ""), False) for request in report.latest),
            log_query=QueryDict(urlencode(log_query)),
            charts=tuple((shown, report.chart(shown)) for shown in ("tokens", "requests")),
            expiry=Expiry.of(token.expires_at, now),
            limits=limits,
            rate_windows=rate_windows(limits, get_per_token_usage([token.id])[token.id]),
            models=Reach.of(model_names(), token.model_exclusion_list()),
            mcp_servers=Reach.of(configured_mcp_servers(), token.mcp_server_exclusion_list()),
            team=team_of(token),
            own=viewer.may_key(KeyAct.OWN, token),
            can_manage=viewer.may_key(KeyAct.MANAGE, token),
        )
        return context


class TokenCreateView(BaseAqueductView, CreateView):
    model = Token
    form_class = TokenCreateForm
    template_name = "management/create/token.html"
    success_url = reverse_lazy("tokens")  # Redirect to tokens list

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_form(self, form_class=None) -> forms.BaseForm:
        return super().get_form(form_class)

    @transaction.atomic
    def form_valid(self, form) -> HttpResponse:
        # Get the instance without saving to DB yet
        self.object = form.save(commit=False)

        # Assign the user directly to the instance
        self.object.user = self.request.user

        # Call the instance method to generate and set key data
        secret_key = self.object._set_new_key()  # Call method on the instance

        # Now save the fully prepared object to the database
        # This will trigger clean() with the user assigned
        self.object.save()

        show_new_key(self.request, secret_key)

        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = gettext("New API key")
        return context


class TokenEditView(BaseAqueductView, UpdateView):
    model = Token
    form_class = TokenCreateForm
    template_name = "management/edit/token.html"
    pk_url_kwarg = "id"

    def get_queryset(self) -> QuerySet:
        return personal_tokens(self.request.user)

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self) -> str:
        return reverse("token_detail", kwargs={"id": self.object.id})

    def form_valid(self, form) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(
            self.request, gettext("API key %(name)s saved.") % {"name": self.object.name}
        )
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = self.get_success_url()
        return context


class TokenDeleteView(BaseAqueductView, DeleteView):
    model = Token
    pk_url_kwarg = "id"
    template_name = "management/common/confirm_delete.html"
    success_url = reverse_lazy("tokens")

    def get_queryset(self) -> QuerySet:
        return personal_tokens(self.request.user)

    def form_valid(self, form) -> HttpResponse:
        token_name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, gettext("API key %(name)s deleted.") % {"name": token_name})
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        return super().get_context_data(
            confirmation=deletion(self.object),
            cancel_url=reverse("token_detail", kwargs={"id": self.object.id}),
            **kwargs,
        )


@method_decorator(require_POST, name="dispatch")
class TokenRegenerateView(BaseAqueductView, View):
    http_method_names: ClassVar[list] = ["post"]

    def post(self, request, *args, **kwargs) -> HttpResponse:
        service = Token.objects.filter(of_teams(self.viewer.teams(TeamAct.OPEN)))
        token = get_object_or_404(
            (personal_tokens(request.user) | service).select_related("service_account__team"),
            pk=kwargs.get("id"),
        )
        team = team_of(token)
        back = reverse("team", kwargs={"id": team.id}) if team else reverse("tokens")
        if team is not None and not self.viewer.may_team(TeamAct.MANAGE, team):
            messages.error(request, ONLY_TEAM_ADMINS)
            return redirect(back)
        new_key = token.regenerate_key()
        show_new_key(request, new_key)
        returns = (reverse("token_detail", kwargs={"id": token.id}), reverse("tokens"))
        return redirect(request.POST["next"] if request.POST.get("next") in returns else back)
