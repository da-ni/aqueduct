"""Checklists where a checked box makes a model or MCP server available.

The database keeps what is unavailable (`excluded_models`, `excluded_mcp_servers`), which is what
the gateway enforces. A model added to the config later is therefore available until someone
clears its box.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django import forms

from gateway.config import get_mcp_config, get_router_config


def configured_models() -> list[str]:
    return [entry["model_name"] for entry in get_router_config().get("model_list", [])]


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
ORDER = (
    "available_models",
    "merge_exclusion_lists",
    "available_mcp_servers",
    "merge_mcp_server_exclusion_lists",
)


class AvailabilityForm(forms.ModelForm):
    """A ModelForm of an org, team, or profile with the two availability checklists.

    A stored name the config no longer has shows as an unchecked box, so a save keeps it
    unavailable and checking the box removes it.
    """

    available_models = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Available models",
        help_text="Clear a box to make the model unavailable.",
    )
    available_mcp_servers = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
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
            if name not in self.cleaned_data:
                continue
            checked = set(self.cleaned_data[name])
            offered = [value for value, _ in self.fields[name].choices]  # type: ignore[attr-defined]
            excluded: list[str] = getattr(self.instance, stored)
            kept = [n for n in excluded if n not in checked]
            cleared = [n for n in offered if n not in checked and n not in kept]
            setattr(self.instance, stored, kept + cleared)
        return super().save(commit)
