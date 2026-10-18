# management/views/service_account.py

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext
from django.views import View
from django.views.generic import CreateView, DeleteView, UpdateView

from management.access import TeamAct
from management.deletion import deletion
from management.forms import MemberEmailForm, ServiceAccountForm
from management.models import ServiceAccount, Token
from management.views.base import BaseServiceAccountView, BaseTeamView, show_new_key

User = get_user_model()

OWNER_CHOICES = 50


class ServiceAccountCreateView(BaseTeamView, CreateView):
    """
    Handles creation of Service Accounts within a specific Team.
    Requires Team Admin privileges for that team.
    Also creates an initial API key owned by the creator.
    """

    model = ServiceAccount
    form_class = ServiceAccountForm
    template_name = "management/create/service_account.html"
    team_act = TeamAct.MANAGE

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.team
        return kwargs

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["team"] = self.team
        context["view_title"] = gettext("Add a service account")
        context["cancel_url"] = reverse("team", kwargs={"id": self.team.id})
        return context

    @transaction.atomic
    def form_valid(self, form) -> HttpResponse:
        form.instance.team = self.team
        self.object = form.save()
        token = Token(
            name=self.object.name,
            user=self.request.user,
            service_account=self.object,
            expires_at=form.cleaned_data.get("token_expires_at"),
        )
        secret_key = token._set_new_key()
        token.save()
        show_new_key(self.request, secret_key)
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        return reverse("team", kwargs={"id": self.team.id})


class ServiceAccountDeleteView(BaseServiceAccountView, DeleteView):
    template_name = "management/common/confirm_delete.html"
    context_object_name = "object"  # Used by DeleteView template

    def get_success_url(self) -> str:
        """Redirect back to the team detail page after deletion."""
        return reverse("team", kwargs={"id": self.team.id})

    def get_context_data(self, **kwargs) -> dict[str, object]:
        return super().get_context_data(
            confirmation=deletion(self.object), cancel_url=self.get_success_url(), **kwargs
        )

    def form_valid(self, form) -> HttpResponse:
        name = self.object.name
        response = super().form_valid(form)
        messages.success(
            self.request, gettext("Service account %(name)s deleted.") % {"name": name}
        )
        return response


class ServiceAccountUpdateView(BaseServiceAccountView, UpdateView):
    """
    Handles editing an existing Service Account (name, description).
    Requires Team Admin privileges for the SA's team.
    Uses ServiceAccountForm for validation.
    """

    context_object_name = "service_account"  # Match template usage
    form_class = ServiceAccountForm
    template_name = "management/edit/service_account.html"

    def get_initial(self) -> dict:
        initial = super().get_initial()
        try:
            token = Token.objects.get(service_account=self.get_object())
            if token.expires_at:
                # Format for datetime-local input
                initial["token_expires_at"] = token.expires_at.strftime("%Y-%m-%dT%H:%M")
        except Token.DoesNotExist:
            pass
        return initial

    def get_form_kwargs(self) -> dict:
        """Pass the team instance to the form for validation (e.g., SA count limit)."""
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.team
        return kwargs

    def form_valid(self, form) -> HttpResponse:
        """Adds a success message upon successful update."""
        response = super().form_valid(form)
        # Update the token's expiration date if provided
        expires_at = form.cleaned_data.get("token_expires_at")
        try:
            token = Token.objects.get(service_account=self.object)
            if expires_at:
                token.expires_at = expires_at
            else:
                token.expires_at = None
            token.save(update_fields=["expires_at"])
        except Token.DoesNotExist:
            pass  # Optionally handle this case
        messages.success(
            self.request, gettext("Service account %(name)s saved.") % {"name": self.object.name}
        )
        return response

    def get_success_url(self) -> str:
        """Redirect back to the team detail page after successful edit."""
        return reverse("team", kwargs={"id": self.team.id})

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = gettext("Edit service account")
        context["cancel_url"] = self.get_success_url()  # Add cancel URL
        # context['service_account'] is set automatically by UpdateView
        context["team"] = self.team
        return context


class ServiceAccountTransferOwnershipView(BaseServiceAccountView, View):
    template_name = "management/transfer_service_account.html"

    def get_success_url(self) -> str:
        return reverse("team", kwargs={"id": self.team.id})

    def owner_form(self, data=None) -> MemberEmailForm:
        team = self.team
        return MemberEmailForm(
            data,
            candidates=team.member_profiles.all(),
            not_found=lambda email: (
                gettext("%(email)s is not a member of %(team)s.")
                % {"email": email, "team": team.name}
            ),
        )

    def render_form(self, form: MemberEmailForm, token: Token | None) -> HttpResponse:
        team = self.team
        owner = token.user if token else None
        others = team.member_profiles.exclude(user=owner)
        choices = None
        if team.member_profiles.count() <= OWNER_CHOICES:
            choices = list(others.order_by("user__email").values_list("user__email", flat=True))
        context = {
            "service_account": self.get_object(),
            "team": team,
            "form": form,
            "current_owner_email": owner.email if owner else None,
            "has_other_members": others.exists(),
            "owner_choices": choices,
            "cancel_url": self.get_success_url(),
            "view_title": gettext("Transfer ownership"),
        }
        return TemplateResponse(request=self.request, template=self.template_name, context=context)

    def get(self, request, *args, **kwargs) -> HttpResponse:
        token = Token.objects.filter(service_account=self.get_object()).select_related("user")
        return self.render_form(self.owner_form(), token.first())

    @method_decorator(transaction.atomic)
    def post(self, request, *args, **kwargs) -> HttpResponse:
        sa = self.get_object()
        token = Token.objects.select_for_update().filter(service_account=sa).first()
        if token is None:
            messages.error(
                request,
                gettext("Service account %(name)s has no key to transfer.") % {"name": sa.name},
            )
            return redirect(self.get_success_url())
        form = self.owner_form(request.POST)
        if form.is_valid() and form.profile.user_id == token.user_id:
            form.add_error(
                "email",
                gettext("%(email)s already owns this key.") % {"email": form.profile.user.email},
            )
        if not form.is_valid():
            return self.render_form(form, token)
        token.user = form.profile.user
        token.save(update_fields=["user"])
        messages.success(
            request,
            gettext("%(email)s now owns the key of %(name)s.")
            % {"email": form.profile.user.email, "name": sa.name},
        )
        return redirect(self.get_success_url())
