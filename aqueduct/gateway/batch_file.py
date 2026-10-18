import json
from collections.abc import Collection
from dataclasses import dataclass

import litellm

from gateway.config import get_router, model_names, resolve_model_alias


@dataclass(frozen=True)
class BatchFile:
    content: bytes
    models: frozenset[str]
    preview: str


def _relay_name(model: str) -> str | None:
    deployment: litellm.Deployment | None = get_router().get_deployment(model_id=model)
    params = getattr(deployment, "litellm_params", None)
    deployment_model = getattr(params, "model", None)
    if not deployment_model:
        return None
    relay: str
    relay, _, _, _ = litellm.get_llm_provider(
        deployment_model, custom_llm_provider=getattr(params, "custom_llm_provider", None)
    )
    return relay


def parse_batch_file(content: bytes, excluded_models: Collection[str]) -> BatchFile:
    """Validate a batch input file and rewrite each request's model to its upstream name.

    Raises ValueError naming the first line or request that fails.
    """
    text = content.decode("utf-8").splitlines()
    lines = []
    custom_ids = set()
    models = set()
    for i, line in enumerate(text, start=1):
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as err:
            raise ValueError(f"Invalid JSON at line {i}") from err
        if not isinstance(request, dict):
            raise ValueError(f"Line {i} is not a JSON object")  # noqa: TRY004
        if request.get("custom_id") in (None, ""):
            raise ValueError(f"No custom_id found at line {i}")
        # The OpenAI Batch API requires a string custom_id.
        custom_id = request["custom_id"] = str(request["custom_id"])
        if custom_id in custom_ids:
            raise ValueError(f"Duplicate custom_id found at line {i}")
        custom_ids.add(custom_id)
        body = request.get("body")
        if isinstance(body, dict) and "model" in body:
            requested = body["model"]
            model = resolve_model_alias(requested) if isinstance(requested, str) else None
            relay = _relay_name(model) if model else None
            if model is None or relay is None or model in excluded_models:
                raise ValueError(f"Unknown model '{requested}' for custom_id '{custom_id}'")
            models.add(model)
            body["model"] = relay
        lines.append(json.dumps(request, separators=(",", ":")))
    return BatchFile(
        content=("\n".join(lines) + "\n").encode("utf-8"),
        models=frozenset(models),
        preview="\n".join(text[:10]),
    )


def unusable_model(models: Collection[str], excluded_models: Collection[str]) -> str | None:
    configured = model_names()
    return next((m for m in sorted(models) if m not in configured or m in excluded_models), None)
