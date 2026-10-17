"""Checklists where a checked box makes a model or MCP server available.

The database keeps what is unavailable (`excluded_models`, `excluded_mcp_servers`), which is what
the gateway enforces. A model added to the config later is therefore available until someone
clears its box.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django import forms
from django.utils.html import format_html
from django.utils.safestring import SafeString

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
    """How many of the configured names one key may use."""

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
"""Each checklist field, the list it stores to, and the names the config has."""
INHERIT_LABELS = {
    "merge_exclusion_lists": "Also apply the organization's unavailable models",
    "merge_mcp_server_exclusion_lists": "Also apply the organization's unavailable MCP servers",
}
INHERIT_HELP = {
    "merge_exclusion_lists": "Clear this to use only the boxes above.",
    "merge_mcp_server_exclusion_lists": "Clear this to use only the boxes above.",
}
"""Labels of the merge flags of a team or profile, whose parent is the organization."""
ORDER = (
    "available_models",
    "merge_exclusion_lists",
    "available_mcp_servers",
    "merge_mcp_server_exclusion_lists",
)


class Checklist(forms.CheckboxSelectMultiple):
    """Also posts `<name>-shown`, so the form can tell a list cleared of every box from a list
    it did not render. Without the marker a save leaves the stored list as it is."""

    def render(self, name, value, attrs=None, renderer=None) -> SafeString:  # type: ignore[no-untyped-def]
        checklist = super().render(name, value, attrs, renderer)
        return format_html('{}<input type="hidden" name="{}-shown" value="1">', checklist, name)


class AvailabilityForm(forms.ModelForm):
    """A ModelForm of an org, team, or profile with the two availability checklists.

    A stored name the config no longer has shows as an unchecked box, so a save keeps it
    unavailable and checking the box removes it.
    """

    available_models = forms.MultipleChoiceField(
        required=False,
        widget=Checklist,
        label="Available models",
        help_text="Clear a box to make the model unavailable.",
    )
    available_mcp_servers = forms.MultipleChoiceField(
        required=False,
        widget=Checklist,
        label="Available MCP servers",
        help_text="Clear a box to make the MCP server unavailable.",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Each checklist, then whether the parent's choices also apply, after the other fields.
        for name in ORDER:
            if name in self.fields:
                self.fields[name] = self.fields.pop(name)
        for name, (stored, configured) in CHECKLISTS.items():
            if name not in self.fields:
                continue
            excluded: list[str] = getattr(self.instance, stored)
            known = configured()
            missing = [n for n in excluded if n not in known]
            self.fields[name].choices = [(n, n) for n in known] + [  # type: ignore[attr-defined]
                (n, f"{n} (not in the current config)") for n in missing
            ]
            self.initial[name] = [n for n in known if n not in excluded]

    def save(self, commit: bool = True) -> Any:
        for name, (stored, _) in CHECKLISTS.items():
            if name not in self.cleaned_data or f"{self.add_prefix(name)}-shown" not in self.data:
                continue
            checked = set(self.cleaned_data[name])
            offered = [value for value, _ in self.fields[name].choices]  # type: ignore[attr-defined]
            excluded: list[str] = getattr(self.instance, stored)
            kept = [n for n in excluded if n not in checked]
            cleared = [n for n in offered if n not in checked]
            setattr(self.instance, stored, list(dict.fromkeys(kept + cleared)))
        return super().save(commit)
