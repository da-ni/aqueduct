import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.core.management.base import CommandError

from gateway.config import model_names
from management.model_catalog import hugging_face_repo, quantization_in

DATALAB_PROFILE = Path(__file__).with_name("datalab.yaml")


class Kind(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    SPEECH = "speech"
    TRANSCRIPTION = "transcription"
    IMAGE = "image"


@dataclass(frozen=True)
class Spread:
    median: float
    sigma: float
    cap: int = 2**31 - 1

    def draw(self, rng: random.Random) -> int:
        if self.median <= 0:
            return 0
        return min(self.cap, int(rng.lognormvariate(math.log(self.median), self.sigma)))


ROUTER_MODES = {
    Kind.CHAT: "chat",
    Kind.EMBEDDING: "embedding",
    Kind.TRANSCRIPTION: "audio_transcription",
    Kind.SPEECH: "audio_speech",
    Kind.IMAGE: "image_generation",
}
API_PATHS = {
    Kind.CHAT: "/v1/chat/completions",
    Kind.EMBEDDING: "/v1/embeddings",
    Kind.TRANSCRIPTION: "/v1/audio/transcriptions",
    Kind.SPEECH: "/v1/audio/speech",
    Kind.IMAGE: "/v1/images/generations",
}
Z90 = 1.2816


@dataclass(frozen=True)
class KindDefaults:
    input_tokens: Spread
    output_tokens: Spread
    latency_ms: Spread
    cached_share: float = 0


def kind_defaults(raw: dict[str, Any]) -> dict[Kind, KindDefaults]:
    return {
        Kind(kind): KindDefaults(
            input_tokens=Spread(**figures["input_tokens"]),
            output_tokens=Spread(**figures["output_tokens"]),
            latency_ms=Spread(**figures["latency_ms"]),
            cached_share=figures.get("cached_share", 0),
        )
        for kind, figures in raw.items()
    }


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: Kind
    weight: float
    reasoning: bool
    input_tokens: Spread
    output_tokens: Spread
    latency_ms: Spread
    cached_share: float


def model_spec(entry: dict[str, Any], kinds: dict[Kind, KindDefaults]) -> ModelSpec:
    kind = Kind(entry["kind"])
    defaults = kinds[kind]
    latency = defaults.latency_ms
    if "latency_ms" in entry:
        p50, p90 = (float(v) for v in entry["latency_ms"])
        latency = replace(latency, median=p50, sigma=math.log(p90 / p50) / Z90)
    spec = ModelSpec(
        name=entry["name"],
        kind=kind,
        weight=float(entry["weight"]),
        reasoning=bool(entry.get("reasoning")),
        input_tokens=replace(
            defaults.input_tokens, median=entry.get("input_tokens", defaults.input_tokens.median)
        ),
        output_tokens=replace(
            defaults.output_tokens, median=entry.get("output_tokens", defaults.output_tokens.median)
        ),
        latency_ms=latency,
        cached_share=float(entry.get("cached_share", defaults.cached_share)),
    )
    if spec.weight <= 0 or latency.sigma < 0 or not 0 <= spec.cached_share <= 1:
        msg = "weight must be positive, latency_ms rising, and cached_share between 0 and 1"
        raise ValueError(msg)
    return spec


def read_model_mix(path: Path, kinds: dict[Kind, KindDefaults]) -> list[ModelSpec]:
    try:
        models = [model_spec(m, kinds) for m in yaml.safe_load(path.read_text())["models"]]
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        msg = (
            f"Cannot read {path}: {exc!r}. Expected `models:` as a list like "
            f"{DATALAB_PROFILE}, kind one of {', '.join(Kind)}."
        )
        raise CommandError(msg) from exc
    if not models:
        msg = f"{path} lists no models."
        raise CommandError(msg)
    return models


def require_served(models: Sequence[ModelSpec]) -> None:
    missing = sorted({m.name for m in models} - set(model_names()))
    if missing:
        msg = (
            f"Not in the router config ({settings.LITELLM_ROUTER_CONFIG_FILE_PATH}): "
            f"{', '.join(missing)}. Requests may only name served models. Pass "
            "--write-router-config <path> to write a router config that serves them."
        )
        raise CommandError(msg)


def route_for(model: ModelSpec) -> str:
    return f"openai/{model.name}"


def model_info_for(model: ModelSpec) -> dict[str, Any]:
    info: dict[str, Any] = {"mode": ROUTER_MODES[model.kind]}
    if repo := hugging_face_repo(route_for(model), model.name):
        info["model_card_url"] = f"https://huggingface.co/{repo}"
    if quantization := quantization_in(model.name):
        info["quantization"] = quantization
    return info


def router_config_for(models: Sequence[ModelSpec]) -> dict[str, Any]:
    return {
        "model_list": [
            {
                "model_name": m.name,
                "litellm_params": {"model": route_for(m), "api_key": "os.environ/OPENAI_API_KEY"},
                "model_info": model_info_for(m),
            }
            for m in models
        ]
    }
