from typing import ClassVar

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from gateway.config import model_names
from gateway.rate_limiting import get_per_token_usage
from management.availability import Reach, configured_mcp_servers
from management.deletion import deletion
from management.forms import TokenCreateForm
from management.key_activity import (
    Expiry,
    KeyAccess,
    KeyKind,
    key_list,
    key_report,
    personal_tokens,
)
from management.models import Token
from management.ownership import RELATED, is_personal_of, of_teams, org_id, team_of
from management.request_log import Key, Row
from management.views.base import ONLY_TEAM_ADMINS, BaseAqueductView


class UserTokensView(BaseAqueductView, TemplateView):
    template_name = "management/tokens.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        kind = KeyKind.parse(self.request.GET.get("kind", ""))
        query = self.request.GET.get("q", "").strip()
        max_tokens = getattr(settings, "MAX_USER_TOKENS", 3)
        keys = key_list(self.profile, kind, query, timezone.now())
        context.update(
            keys=keys,
            kinds=tuple(KeyKind),
            kind=kind,
            query=query,
            max_tokens=max_tokens,
            can_add_token=keys.personal_count < max_tokens,
        )
        return context


class TokenDetailView(BaseAqueductView, DetailView):
    model = Token
    template_name = "management/token_detail.html"
    pk_url_kwarg = "id"
    context_object_name = "token"

    def get_queryset(self) -> QuerySet:
        return KeyAccess.of(self.profile).tokens().select_related(*RELATED)

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        token = self.object
        now = timezone.now()
        report = key_report(token, now)
        team = team_of(token)
        own = is_personal_of(token, self.request.user.id)
        metric = "requests" if self.request.GET.get("metric") == "requests" else "tokens"
        log_query: dict[str, object] = {"key": token.id, "span": "1m"}
        if self.profile.is_admin() and self.viewed_org is not None:
            key_org = org_id(token)
            if key_org != self.viewed_org.id:
                log_query["org"] = key_org or "all"
        context.update(
            report=report,
            latest=tuple(Row(request, Key(token, None, ""), False) for request in report.latest),
            metric=metric,
            log_query=urlencode(log_query),
            charts=tuple((shown, report.chart(shown)) for shown in ("tokens", "requests")),
            expiry=Expiry.of(token.expires_at, now),
            limits=token.get_limit(),
            usage=get_per_token_usage([token.id])[token.id],
            models=Reach.of(model_names(), token.model_exclusion_list()),
            mcp_servers=Reach.of(configured_mcp_servers(), token.mcp_server_exclusion_list()),
            team=team,
            own=own,
            can_manage=self.profile.is_team_admin(team) if team else own,
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

        messages.info(self.request, f"{secret_key}", extra_tags="token-regenerated-key")

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
        service = Token.objects.filter(of_teams(self.profile.openable_teams()))
        token = get_object_or_404(
            (personal_tokens(request.user) | service).select_related("service_account__team"),
            pk=kwargs.get("id"),
        )
        team = team_of(token)
        back = reverse("team", kwargs={"id": team.id}) if team else reverse("tokens")
        if team is not None and not self.profile.is_team_admin(team):
            messages.error(request, ONLY_TEAM_ADMINS)
            return redirect(back)
        new_key = token.regenerate_key()
        messages.info(request, new_key, extra_tags="token-regenerated-key")
        returns = (reverse("token_detail", kwargs={"id": token.id}), reverse("tokens"))
        return redirect(request.POST["next"] if request.POST.get("next") in returns else back)
