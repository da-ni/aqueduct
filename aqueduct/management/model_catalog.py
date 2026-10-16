import json
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum, auto
from typing import Any

from django.contrib.auth.models import AbstractUser
from django.db.models import Model
from django.urls import reverse

from gateway.config import get_router_config, model_info_with_context
from management.models import Token, UserProfile


class Category(StrEnum):
    CHAT = auto()
    EMBEDDINGS = auto()
    AUDIO = auto()
    IMAGES = auto()
    OTHER = auto()

    @property
    def label(self) -> str:
        return self.value.title()

    @classmethod
    def parse(cls, raw: str) -> "Category | None":
        try:
            return cls(raw)
        except ValueError:
            return None


@dataclass(frozen=True)
class Mode:
    category: Category
    label: str
    spellings: tuple[str, ...]
    endpoint: str | None = None
    fields: tuple[tuple[str, object], ...] = ()
    form: bool = False
    output: str | None = None
    context_label: str = "Context window"


MODES: tuple[Mode, ...] = (
    Mode(
        Category.CHAT,
        "Chat",
        ("chat",),
        "gateway:chat_completions",
        (("messages", [{"role": "user", "content": "Hello"}]),),
    ),
    Mode(
        Category.CHAT, "Completion", ("completion",), "gateway:completions", (("prompt", "Hello"),)
    ),
    Mode(Category.CHAT, "Responses", ("responses",), "gateway:responses", (("input", "Hello"),)),
    Mode(
        Category.EMBEDDINGS,
        "Embeddings",
        ("embedding", "embeddings"),
        "gateway:embeddings",
        (("input", "Hello"),),
        context_label="Max input tokens",
    ),
    Mode(
        Category.AUDIO,
        "Text to speech",
        ("audio_speech",),
        "gateway:speech",
        (("input", "Hello"), ("voice", "alloy")),
        output="speech.mp3",
    ),
    Mode(
        Category.AUDIO,
        "Speech to text",
        ("audio_transcription", "audio_transcriptions"),
        "gateway:transcriptions",
        (("file", "@speech.mp3"),),
        form=True,
    ),
    Mode(
        Category.IMAGES,
        "Images",
        ("image_generation", "image_generations"),
        "gateway:image_generation",
        (("prompt", "A lighthouse at dawn"),),
    ),
)
OTHER_MODE = Mode(Category.OTHER, "Other", ())
_MODE_BY_SPELLING = {spelling: mode for mode in MODES for spelling in mode.spellings}


def mode_for(raw: object) -> Mode:
    # LiteLLM treats a model without a mode as chat.
    if raw is None or raw == "":
        return _MODE_BY_SPELLING["chat"]
    return _MODE_BY_SPELLING.get(str(raw).strip().lower(), OTHER_MODE)


def _count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


_MILLION, _THOUSAND = 1_000_000, 1_000


def _tokens(value: object) -> str:
    count = _count(value)
    if count is None:
        return ""
    if count >= _MILLION:
        return f"{count / _MILLION:.1f}".removesuffix(".0") + "M"
    if count >= _THOUSAND:
        return f"{round(count / _THOUSAND)}K"
    return str(count)


def _number(value: object) -> str:
    count = _count(value)
    return "" if count is None else f"{count:,}"


def _modalities(value: object) -> str:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        return ""
    return ", ".join(value).capitalize()


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


_SPECS: tuple[tuple[str, str, Callable[[object], str]], ...] = (
    ("Max output tokens", "max_output_tokens", _tokens),
    ("Dimensions", "output_vector_size", _number),
    ("Input", "supported_modalities", _modalities),
    ("Output", "supported_output_modalities", _modalities),
)
_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("supports_vision", "Vision"),
    ("supports_function_calling", "Tools"),
    ("supports_response_schema", "JSON schema"),
    ("supports_reasoning", "Reasoning"),
    ("supports_web_search", "Web search"),
    ("supports_pdf_input", "PDF input"),
    ("supports_audio_input", "Audio input"),
    ("supports_audio_output", "Audio output"),
    ("supports_prompt_caching", "Prompt caching"),
    ("supports_native_streaming", "Streaming"),
)


@dataclass(frozen=True)
class Spec:
    label: str
    value: str


@dataclass(frozen=True)
class Scope:
    team: str | None
    excluded: frozenset[str]
    admin_url: str | None = None

    @property
    def name(self) -> str:
        return f"{self.team} service accounts" if self.team else "your personal keys"


def _and_join(items: list[str]) -> str:
    head, tail = items[:-1], items[-1:]
    return " and ".join([", ".join(head), *tail] if head else tail)


@dataclass(frozen=True)
class ModelCard:
    name: str
    aliases: tuple[str, ...]
    mode: Mode
    served_model: str | None
    context: str | None
    specs: tuple[Spec, ...]
    capabilities: tuple[str, ...]
    deprecation_date: date | None
    off_for: tuple[Scope, ...]
    on_for: tuple[Scope, ...]

    @property
    def off_for_all(self) -> bool:
        return bool(self.off_for) and not self.on_for

    @property
    def off_badge(self) -> str:
        if not self.off_for:
            return ""
        if self.off_for_all:
            return "Off for you"
        first, more = self.off_for[0], len(self.off_for) - 1
        return f"Off for {first.team or 'you'}" + (f" +{more}" if more else "")

    @property
    def off_names(self) -> str:
        return _and_join([scope.name for scope in self.off_for])

    @property
    def on_names(self) -> str:
        return _and_join([scope.name for scope in self.on_for])

    def matches(self, text: str) -> bool:
        needle = text.casefold()
        return any(needle in key.casefold() for key in (self.name, *self.aliases))

    def curl(self, base_url: str) -> str | None:
        mode = self.mode
        if mode.endpoint is None:
            return None
        fields = {"model": self.name, **dict(mode.fields)}
        lines = [
            f"curl {base_url}{reverse(mode.endpoint)}",
            '-H "Authorization: Bearer $AQUEDUCT_API_KEY"',
        ]
        if mode.form:
            lines += [f"-F {shlex.quote(f'{key}={value}')}" for key, value in fields.items()]
        else:
            members = ",\n".join(f"    {json.dumps(k)}: {json.dumps(v)}" for k, v in fields.items())
            body = "{\n" + members + "\n  }"
            lines += ['-H "Content-Type: application/json"', f"-d {shlex.quote(body)}"]
        if mode.output:
            lines.append(f"--output {mode.output}")
        return " \\\n  ".join(lines)


@dataclass(frozen=True)
class Catalog:
    cards: tuple[ModelCard, ...]

    @property
    def categories(self) -> tuple[Category, ...]:
        present = {card.mode.category for card in self.cards}
        return tuple(category for category in Category if category in present)

    @property
    def has_context(self) -> bool:
        return any(card.context for card in self.cards)

    def search(self, category: Category | None, text: str) -> tuple[ModelCard, ...]:
        return tuple(
            card
            for card in self.cards
            if category in (None, card.mode.category) and card.matches(text)
        )

    def find(self, key: str) -> ModelCard | None:
        return next((card for card in self.cards if key in (card.name, *card.aliases)), None)


def parse_card(entry: Mapping[str, Any], scopes: Sequence[Scope] = ()) -> ModelCard:
    info = model_info_with_context(entry)
    name: str = entry["model_name"]
    mode = mode_for(info.get("mode"))
    context = _tokens(info.get("max_input_tokens"))
    cells = [(mode.context_label, context)]
    cells += [(label, format_value(info.get(key))) for label, key, format_value in _SPECS]
    capabilities = tuple(label for key, label in _CAPABILITIES if info.get(key) is True)
    multiplier = info.get("request_limit_multiplier")
    if isinstance(multiplier, int | float) and not isinstance(multiplier, bool) and multiplier != 1:
        capabilities += (f"{multiplier:g}x request limit",)
    served = (entry.get("litellm_params") or {}).get("model")
    return ModelCard(
        name=name,
        aliases=tuple(info.get("aliases") or ()),
        mode=mode,
        served_model=served if isinstance(served, str) and served else None,
        context=context or None,
        specs=tuple(Spec(label, value) for label, value in cells if value),
        capabilities=capabilities,
        deprecation_date=_date(info.get("deprecation_date")),
        off_for=tuple(scope for scope in scopes if name in scope.excluded),
        on_for=tuple(scope for scope in scopes if name not in scope.excluded),
    )


def _admin_url(viewer: AbstractUser, obj: Model) -> str | None:
    meta = obj._meta
    if not (viewer.is_staff and viewer.has_perm(f"{meta.app_label}.change_{meta.model_name}")):
        return None
    return reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])


def scopes_for(profile: UserProfile | None) -> tuple[Scope, ...]:
    if profile is None:
        return ()
    viewer = profile.user
    personal = Scope(
        None,
        frozenset(Token.exclusion_list_from_objects(profile, profile.org)),
        _admin_url(viewer, viewer),
    )
    teams = profile.teams.select_related("org").order_by("name")
    return (
        personal,
        *(
            Scope(
                team.name,
                frozenset(Token.exclusion_list_from_objects(team, team.org)),
                _admin_url(viewer, team),
            )
            for team in teams
        ),
    )


def load_catalog(profile: UserProfile | None) -> Catalog:
    scopes = scopes_for(profile)
    entries = get_router_config().get("model_list") or ()
    return Catalog(tuple(parse_card(entry, scopes) for entry in entries))
