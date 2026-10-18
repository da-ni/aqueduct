import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from management.model_catalog import hugging_face_repo, quantization_in


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


@dataclass(frozen=True)
class Shape:
    mode: str
    path: str
    input_tokens: Spread
    output_tokens: Spread
    latency_ms: Spread
    cached_share: float = 0


TOKEN_CONTEXT = 262_144
MAX_OUTPUT = 32_768
LATENCY_CAP = 600_000
SHAPES: dict[Kind, Shape] = {
    Kind.CHAT: Shape(
        "chat",
        "/v1/chat/completions",
        Spread(50_000, 0.6, TOKEN_CONTEXT),
        Spread(330, 2.0, MAX_OUTPUT),
        Spread(5000, 1.5, LATENCY_CAP),
        cached_share=0.9,
    ),
    Kind.EMBEDDING: Shape(
        "embedding", "/v1/embeddings", Spread(350, 1.2, 8191), Spread(0, 0), Spread(110, 0.6)
    ),
    Kind.TRANSCRIPTION: Shape(
        "audio_transcription",
        "/v1/audio/transcriptions",
        Spread(0, 0),
        Spread(450, 0.9, 20_000),
        Spread(2800, 0.7),
    ),
    Kind.SPEECH: Shape(
        "audio_speech", "/v1/audio/speech", Spread(140, 0.8, 4096), Spread(0, 0), Spread(1600, 0.5)
    ),
    Kind.IMAGE: Shape(
        "image_generation",
        "/v1/images/generations",
        Spread(60, 0.6, 1000),
        Spread(0, 0),
        Spread(9000, 0.4),
    ),
}
Z90 = 1.2816


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


def model_spec(entry: dict[str, Any]) -> ModelSpec:
    kind = Kind(entry["kind"])
    shape = SHAPES[kind]
    latency = shape.latency_ms
    if "latency_ms" in entry:
        p50, p90 = (float(v) for v in entry["latency_ms"])
        latency = Spread(p50, math.log(p90 / p50) / Z90, LATENCY_CAP)
    spec = ModelSpec(
        name=entry["name"],
        kind=kind,
        weight=float(entry["weight"]),
        reasoning=bool(entry.get("reasoning")),
        input_tokens=replace(
            shape.input_tokens, median=entry.get("input_tokens", shape.input_tokens.median)
        ),
        output_tokens=replace(
            shape.output_tokens, median=entry.get("output_tokens", shape.output_tokens.median)
        ),
        latency_ms=latency,
        cached_share=float(entry.get("cached_share", shape.cached_share)),
    )
    if spec.weight <= 0 or latency.sigma < 0 or not 0 <= spec.cached_share <= 1:
        msg = "weight must be positive, latency_ms rising, and cached_share between 0 and 1"
        raise ValueError(msg)
    return spec


def read_model_mix(path: Path) -> list[ModelSpec]:
    return [model_spec(m) for m in yaml.safe_load(path.read_text())["models"]]


def route_for(model: ModelSpec) -> str:
    return f"openai/{model.name}"


def model_info_for(model: ModelSpec) -> dict[str, Any]:
    info: dict[str, Any] = {"mode": SHAPES[model.kind].mode}
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
