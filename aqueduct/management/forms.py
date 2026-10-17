from typing import ClassVar

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils import timezone

from .availability import (
    INHERIT_HELP,
    INHERIT_LABELS,
    AvailabilityForm,
    Checklist,
    configured_mcp_servers,
)
from .models import Org, ServiceAccount, Team, Token, UserProfile

NAME_INPUT = "input w-full max-w-md"
DESCRIPTION_INPUT = "textarea w-full max-w-2xl"
EXPIRY_INPUT = "input w-full max-w-xs"
NUMBER_INPUT = "input w-full max-w-xs"
CHECKBOX = "checkbox checkbox-sm"


class ServiceAccountForm(forms.ModelForm):
    token_expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": EXPIRY_INPUT}, format="%Y-%m-%dT%H:%M"
        ),
        label="Key expiry in UTC (optional)",
        help_text="Leave it empty and the key never expires.",
    )

    class Meta:
        model = ServiceAccount
        fields: ClassVar[list] = ["name", "description"]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(
                attrs={"placeholder": "For example rag-pipeline", "class": NAME_INPUT}
            ),
            "description": forms.Textarea(attrs={"rows": 3, "class": DESCRIPTION_INPUT}),
        }
        labels: ClassVar[dict] = {"name": "Name", "description": "Description (optional)"}

    def __init__(self, *args, **kwargs):
        # Pop the team object passed from the view
        self.team = kwargs.pop("team", None)
        super().__init__(*args, **kwargs)
        # Ensure team was passed if required for validation
        if not self.team:
            # This indicates a programming error in the view setup
            raise ValueError("Team instance must be provided to the ServiceAccountForm.")

    def clean(self) -> dict:
        cleaned_data = super().clean()
        name = cleaned_data.get("name")

        # Perform the team limit check here
        if self.team:
            # Check for duplicate name within the team
            if name:
                query = ServiceAccount.objects.filter(team=self.team, name__iexact=name)
                instance_pk = self.instance.pk
                if instance_pk:
                    query = query.exclude(pk=instance_pk)
                if query.exists():
                    self.add_error(
                        "name",
                        f"A service account with the name '{name}' already exists "
                        f"in team '{self.team.name}'.",
                    )

            # Check team limit
            limit = getattr(settings, "MAX_SERVICE_ACCOUNTS_PER_TEAM", 10)
            query = ServiceAccount.objects.filter(team=self.team)

            # Exclude self if updating (form instance has pk if it's an update)
            instance_pk = self.instance.pk
            if instance_pk:
                query = query.exclude(pk=instance_pk)

            current_count = query.count()

            # Check only needs to prevent *adding* a new one if limit is reached
            # For updates, this check isn't strictly needed unless team can change
            if instance_pk is None and current_count >= limit:
                # Attach the error to the form, not a specific field, as it's a general limit issue
                raise ValidationError(
                    f"Team '{self.team.name}' has reached the maximum limit of "
                    f"{limit} service accounts.",
                    code="limit_reached",
                )
        expires_at = cleaned_data.get("token_expires_at")
        if expires_at and expires_at <= timezone.now():
            self.add_error("token_expires_at", "Expiration date must be in the future.")
        return cleaned_data


class TeamCreateForm(forms.ModelForm):
    class Meta:
        model = Team
        fields: ClassVar[list] = ["name", "description"]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(
                attrs={"placeholder": "For example NLP Lab", "class": NAME_INPUT}
            ),
            "description": forms.Textarea(attrs={"rows": 3, "class": DESCRIPTION_INPUT}),
        }
        labels: ClassVar[dict] = {"name": "Name", "description": "Description (optional)"}

    def __init__(self, *args, **kwargs):
        # Pop the organization kwarg before initializing the parent ModelForm
        # We'll receive this from the view
        self.org = kwargs.pop("org", None)
        super().__init__(*args, **kwargs)

    def clean(self) -> dict:
        """
        Perform validation checks that depend on multiple fields or
        require access to data outside the form fields (like organization).
        """
        cleaned_data = super().clean()
        name = cleaned_data.get("name")

        # We need both the name and the organization (passed during init)
        # to perform the uniqueness check
        if name and self.org:
            # Check if a team with this name already exists in this organization
            query = Team.objects.filter(org=self.org, name__iexact=name)

            # If we are updating an existing instance, exclude it from the check
            if self.instance and self.instance.pk:
                query = query.exclude(pk=self.instance.pk)

            if query.exists():
                # Use __iexact for case-insensitive comparison if desired,
                # or just name=name for case-sensitive.
                self.add_error("name", "A team with this name already exists in your organization.")

        return cleaned_data


class TokenCreateForm(forms.ModelForm):
    class Meta:
        model = Token
        fields: ClassVar[list] = ["name", "expires_at"]
        widgets: ClassVar[dict] = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. thesis-eval", "class": NAME_INPUT}),
            "expires_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": EXPIRY_INPUT}, format="%Y-%m-%dT%H:%M"
            ),
        }
        labels: ClassVar[dict] = {"name": "Name", "expires_at": "Expires (UTC, optional)"}

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if not self.user:
            raise ValueError("User must be provided to TokenCreateForm.")
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["expires_at"].widget.attrs["min"] = f"{timezone.now():%Y-%m-%dT%H:%M}"

    def clean(self) -> dict:
        cleaned_data = super().clean()
        from django.conf import settings

        max_tokens = getattr(settings, "MAX_USER_TOKENS", 3)
        token_count = Token.objects.filter(user=self.user, service_account__isnull=True)
        if self.instance.pk:
            token_count = token_count.exclude(pk=self.instance.pk)
        if token_count.count() >= max_tokens:
            raise ValidationError(f"You can only have {max_tokens} API keys.")

        expires_at = cleaned_data.get("expires_at")
        if expires_at and expires_at <= timezone.now():
            self.add_error("expires_at", "Expiration date must be in the future.")

        return cleaned_data


class OrgLimitsForm(forms.ModelForm):
    class Meta:
        model = Org
        fields: ClassVar[list] = [
            "requests_per_minute",
            "input_tokens_per_minute",
            "output_tokens_per_minute",
            "hourly_limit_multiplier",
            "daily_limit_multiplier",
        ]
        widgets: ClassVar[dict] = {
            field: forms.NumberInput(attrs={"class": NUMBER_INPUT, "min": 0}) for field in fields
        }
        labels: ClassVar[dict] = {
            "requests_per_minute": "Requests per minute, per key",
            "input_tokens_per_minute": "Input tokens per minute, per key",
            "output_tokens_per_minute": "Output tokens per minute, per key",
            "hourly_limit_multiplier": "Hourly limit, in per-minute limits",
            "daily_limit_multiplier": "Daily limit, in per-minute limits",
        }
        help_texts: ClassVar[dict] = {
            "requests_per_minute": "Leave it empty for no limit.",
            "input_tokens_per_minute": "Leave it empty for no limit.",
            "output_tokens_per_minute": "Leave it empty for no limit.",
            "hourly_limit_multiplier": "From 1 to 60. Leave it empty for the server default.",
            "daily_limit_multiplier": "From 1 to 1440. Leave it empty for the server default.",
        }


class CheckboxList(Checklist):
    template_name = "management/widgets/checkboxes.html"


MERGE_WIDGETS = {
    "merge_exclusion_lists": forms.CheckboxInput(attrs={"class": CHECKBOX}),
    "merge_mcp_server_exclusion_lists": forms.CheckboxInput(attrs={"class": CHECKBOX}),
}


class ChecklistForm(AvailabilityForm):
    """The availability checklists as the management pages render them. Without an MCP config
    the form has no MCP fields, so a save leaves the stored MCP list as it is."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("available_models", "available_mcp_servers"):
            self.fields[name].widget = CheckboxList(
                attrs={"class": CHECKBOX}, choices=self.fields[name].choices
            )
        if not configured_mcp_servers():
            del self.fields["available_mcp_servers"]
            self.fields.pop("merge_mcp_server_exclusion_lists", None)


class OrgAvailabilityForm(ChecklistForm):
    """What keys in the org may use, limited to the models and MCP servers the gateway knows."""

    class Meta:
        model = Org
        fields: ClassVar[list] = ["merge_exclusion_lists", "merge_mcp_server_exclusion_lists"]
        widgets: ClassVar[dict] = MERGE_WIDGETS
        labels: ClassVar[dict] = {
            "merge_exclusion_lists": "Apply the server's default unavailable models",
            "merge_mcp_server_exclusion_lists": "Apply the server's default unavailable MCP "
            "servers",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["available_models"].help_text = (
            "Clear a box to make the model unavailable to keys in this organization. "
            "A model added to the configuration later starts out available."
        )
        defaults = {
            "merge_exclusion_lists": settings.AQUEDUCT_DEFAULT_MODEL_EXCLUSION_LIST,
            "merge_mcp_server_exclusion_lists": settings.AQUEDUCT_DEFAULT_MCP_SERVER_EXCLUSION_LIST,
        }
        for name, names in defaults.items():
            if name not in self.fields:
                continue
            # Without server defaults the box changes nothing, and leaving it out keeps it.
            if names:
                self.fields[name].help_text = (
                    f"The server configuration makes {', '.join(names)} unavailable everywhere. "
                    "Clear this to make them available in this organization."
                )
            else:
                del self.fields[name]


class TeamAvailabilityForm(ChecklistForm):
    """What the team's service account keys may use."""

    class Meta:
        model = Team
        fields: ClassVar[list] = ["merge_exclusion_lists", "merge_mcp_server_exclusion_lists"]
        widgets: ClassVar[dict] = MERGE_WIDGETS
        labels: ClassVar[dict] = INHERIT_LABELS
        help_texts: ClassVar[dict] = INHERIT_HELP

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["available_models"].help_text = (
            "Clear a box to make the model unavailable to the team's service account keys. "
            "Members' personal keys are not affected."
        )


class MemberEmailForm(forms.Form):
    """Names one of `candidates` by email, ignoring case. After validation `profile` is that
    person. `not_found` is the error for an email none of them uses, with `{email}` in it."""

    email = forms.EmailField(label="Email")

    def __init__(self, *args, candidates: QuerySet[UserProfile], not_found: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.candidates = candidates
        self.not_found = not_found
        self.profile: UserProfile | None = None

    def clean_email(self) -> str:
        email = self.cleaned_data["email"]
        matches = list(self.candidates.filter(user__email__iexact=email).select_related("user")[:2])
        if not matches:
            raise ValidationError(self.not_found.format(email=email))
        if len(matches) > 1:
            raise ValidationError(f"More than one account uses {email}.")
        self.profile = matches[0]
        return email
