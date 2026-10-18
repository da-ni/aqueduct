from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django import forms
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from gateway.config import get_mcp_config, get_router_config


def configured_models() -> list[str]:
    # LiteLLM balances load over entries that repeat a `model_name`.
    entries = get_router_config().get("model_list", [])
    return list(dict.fromkeys(entry["model_name"] for entry in entries))


def configured_mcp_servers() -> list[str]:
    try:
        return list(get_mcp_config())
    except RuntimeError:
        return []


@dataclass(frozen=True)
class Reach:
    configured: int
    usable: int

    @property
    def every(self) -> bool:
        return self.usable == self.configured

    @classmethod
    def of(cls, configured: list[str], excluded: list[str]) -> "Reach":
        return cls(len(configured), len(set(configured) - set(excluded)))


CHECKLISTS: dict[str, tuple[str, Callable[[], list[str]]]] = {
    "available_models": ("excluded_models", configured_models),
    "available_mcp_servers": ("excluded_mcp_servers", configured_mcp_servers),
}
INHERIT_LABELS = {
    "merge_exclusion_lists": _("Also apply the organization's unavailable models"),
    "merge_mcp_server_exclusion_lists": _("Also apply the organization's unavailable MCP servers"),
}
INHERIT_HELP = {
    "merge_exclusion_lists": _("Clear this to use only the boxes above."),
    "merge_mcp_server_exclusion_lists": _("Clear this to use only the boxes above."),
}
ORDER = (
    "available_models",
    "merge_exclusion_lists",
    "available_mcp_servers",
    "merge_mcp_server_exclusion_lists",
)


class Checklist(forms.CheckboxSelectMultiple):
    def render(self, name, value, attrs=None, renderer=None) -> SafeString:
        checklist = super().render(name, value, attrs, renderer)
        return format_html('{}<input type="hidden" name="{}-shown" value="1">', checklist, name)


class AvailabilityForm(forms.ModelForm):
    available_models = forms.MultipleChoiceField(
        required=False,
        widget=Checklist,
        label=_("Available models"),
        help_text=_("Clear a box to make the model unavailable."),
    )
    available_mcp_servers = forms.MultipleChoiceField(
        required=False,
        widget=Checklist,
        label=_("Available MCP servers"),
        help_text=_("Clear a box to make the MCP server unavailable."),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in ORDER:
            if name in self.fields:
                self.fields[name] = self.fields.pop(name)
        for name, (stored, configured) in CHECKLISTS.items():
            if name not in self.fields:
                continue
            excluded: list[str] = getattr(self.instance, stored)
            known = configured()
            missing = [n for n in excluded if n not in known]
            self.fields[name].choices = [(n, n) for n in known] + [
                (n, gettext("%(name)s (not in the current config)") % {"name": n}) for n in missing
            ]
            self.initial[name] = [n for n in known if n not in excluded]

    def save(self, commit: bool = True) -> Any:
        for name, (stored, _configured) in CHECKLISTS.items():
            if name not in self.cleaned_data or f"{self.add_prefix(name)}-shown" not in self.data:
                continue
            checked = set(self.cleaned_data[name])
            offered = [value for value, _ in self.fields[name].choices]
            excluded: list[str] = getattr(self.instance, stored)
            kept = [n for n in excluded if n not in checked]
            cleared = [n for n in offered if n not in checked]
            setattr(self.instance, stored, list(dict.fromkeys(kept + cleared)))
        return super().save(commit)
