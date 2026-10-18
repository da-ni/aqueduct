import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http import HTTPStatus
from itertools import accumulate, chain
from typing import NamedTuple
from zoneinfo import ZoneInfo

from management.demo.catalog import SHAPES, Kind, ModelSpec, Spread

MODELLESS = (
    ("GET", "/v1/models", 70),
    ("GET", "/v1/files", 8),
    ("POST", "/v1/files", 8),
    ("GET", "/v1/batches", 6),
    ("GET", "/v1/vector_stores", 5),
    ("GET", "/models", 3),
)
MODELLESS_CUM = list(accumulate(w for *_, w in MODELLESS))
MODELLESS_SHARE = 0.05
MODELLESS_NOT_FOUND = 0.02
MODELLESS_MS = Spread(40, 0.8, 30_000)
PRIMARY_MODEL_SHARE = 0.6
CACHE_BLOCK = 128
CACHE_NOISE = 0.08
RESPONSES_API_SHARE = 0.1
OTHER_AGENT_SHARE = 0.1
PROCESSING_MS = Spread(6, 0.6, 5000)
FAILED_MS = (Spread(120, 1.0, 600_000), Spread(30, 1.0, 10_000))
FAILURES = (
    (0.006, (400, 400, 401, 404, 413, 422)),
    (0.0015, (500, 502, 503, 504)),
    (0.001, (499,)),
)
THROTTLED = (0.01, 0.04)
BUSY_WEIGHT = 20
UNFINISHED_WINDOW = timedelta(seconds=90)
UNFINISHED_SHARE = 0.6

LOCAL_HOUR_LOAD: tuple[float, ...] = (
    *(2303, 2222, 2143, 2144, 1976, 1797, 1673, 1545, 1704, 2120, 2898, 3160),
    *(3601, 3534, 3177, 3282, 3372, 3460, 3219, 3003, 2521, 2673, 2466, 2201),
)
WEEKDAY_LOAD = (1, 1, 1, 1, 1, 0.8, 0.6)
DAY_NOISE = (0.75, 1.3)
SERVICE_SHARE = 0.4
PERSONAL_KEY_CAP = 0.025

USER_AGENTS = (
    "OpenAI/Python 1.82.0",
    "OpenAI/Python 1.99.9",
    "AsyncOpenAI/Python 1.97.1",
    "langchain-openai/0.3.28",
    "OpenAI/JS 5.10.2",
    "python-requests/2.32.4",
    "curl/8.5.0",
    "LibreChat/0.7.9",
    "Continue/1.0.21 (vscode)",
    "litellm/1.74.3",
)


class LogRow(NamedTuple):
    token_id: int
    model: str
    timestamp: datetime
    method: str
    status_code: int | None
    response_time_ms: int | None
    processing_time_ms: int | None
    user_agent: str
    ip_address: str
    path: str
    user_id: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class KeyTraffic:
    id: int
    weight: float
    primary_model: ModelSpec
    user_agent: str
    ip_address: str
    user_ids: Sequence[str]
    user_id_share: float
    throttled: float


@dataclass
class SeededKeys:
    personal: list[KeyTraffic] = field(default_factory=list)
    service: list[KeyTraffic] = field(default_factory=list)


class Slot(NamedTuple):
    start: datetime
    seconds: float
    load: float


def log_size(requests_per_day: int, days: int) -> int:
    return round(requests_per_day * days / (1 - MODELLESS_SHARE))


def capped(weights: list[float], share: float) -> list[float]:
    if share * len(weights) <= 1:
        return weights
    for _ in range(100):
        cap = share * sum(weights)
        if max(weights) <= cap * 1.001:
            break
        weights = [min(w, cap) for w in weights]
    return weights


class Traffic:
    def __init__(
        self, rng: random.Random, models: Sequence[ModelSpec], now: datetime, zone: ZoneInfo
    ) -> None:
        self.rng = rng
        self.models = models
        self.model_weights = list(accumulate(m.weight for m in models))
        self.now = now
        self.zone = zone

    def log(self, keys: SeededKeys, start: datetime, total: int) -> Iterator[LogRow]:
        slots = self.hours(start)
        service = round(total * SERVICE_SHARE) if keys.service else 0
        personal = total - service
        personal_cap = PERSONAL_KEY_CAP * total / max(personal, 1)
        return chain(
            self.rows(keys.personal, personal, slots, personal_cap),
            self.rows(keys.service, service, slots, 1.0),
        )

    def hours(self, start: datetime) -> list[Slot]:
        slots = []
        day_noise: dict[date, float] = {}
        slot = start
        while slot < self.now:
            end = min(
                slot.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), self.now
            )
            local = slot.astimezone(self.zone)
            load = LOCAL_HOUR_LOAD[local.hour] * WEEKDAY_LOAD[local.weekday()]
            noise = day_noise.setdefault(local.date(), self.rng.uniform(*DAY_NOISE))
            seconds = (end - slot).total_seconds()
            slots.append(Slot(slot, seconds, load * noise * seconds / 3600))
            slot = end
        return slots

    def rows(
        self, keys: Sequence[KeyTraffic], total: int, slots: Sequence[Slot], key_cap: float
    ) -> Iterator[LogRow]:
        cum = list(accumulate(capped([k.weight for k in keys], key_cap)))
        if not keys or cum[-1] <= 0:
            return
        scale = total / sum(s.load for s in slots)
        owed = 0.0
        for slot in slots:
            owed += slot.load * scale
            count = round(owed)
            owed -= count
            for key in self.rng.choices(keys, cum_weights=cum, k=count):
                yield self.row(
                    key, slot.start + timedelta(seconds=self.rng.random() * slot.seconds)
                )

    def row(self, key: KeyTraffic, at: datetime) -> LogRow:
        rng = self.rng
        agent = rng.choice(USER_AGENTS) if rng.random() < OTHER_AGENT_SHARE else key.user_agent
        user_id = rng.choice(key.user_ids) if rng.random() < key.user_id_share else ""
        common = (agent, key.ip_address)
        processing: int | None = PROCESSING_MS.draw(rng)
        if rng.random() < MODELLESS_SHARE:
            method, path, _ = rng.choices(MODELLESS, cum_weights=MODELLESS_CUM)[0]
            found = rng.random() >= MODELLESS_NOT_FOUND
            return LogRow(
                key.id,
                "",
                at,
                method,
                HTTPStatus.OK if found else HTTPStatus.NOT_FOUND,
                MODELLESS_MS.draw(rng),
                processing,
                *common,
                path,
                user_id,
            )
        model = key.primary_model
        if rng.random() > PRIMARY_MODEL_SHARE:
            model = rng.choices(self.models, cum_weights=self.model_weights)[0]
        path = SHAPES[model.kind].path
        if model.kind is Kind.CHAT and rng.random() < RESPONSES_API_SHARE:
            path = "/v1/responses"
        status = self.status(key, at)
        latency: int | None
        usage: list[int] = []
        if status is None:
            latency = processing = None
        elif status >= HTTPStatus.BAD_REQUEST:
            latency = FAILED_MS[status == HTTPStatus.TOO_MANY_REQUESTS].draw(rng)
        else:
            latency, *usage = self.served(model)
        return LogRow(
            key.id,
            model.name,
            at,
            "POST",
            status,
            latency,
            processing,
            *common,
            path,
            user_id,
            *usage,
        )

    def status(self, key: KeyTraffic, at: datetime) -> int | None:
        if self.now - at < UNFINISHED_WINDOW and self.rng.random() < UNFINISHED_SHARE:
            return None
        r = self.rng.random() - key.throttled
        if r < 0:
            return HTTPStatus.TOO_MANY_REQUESTS
        for share, codes in FAILURES:
            r -= share
            if r < 0:
                return self.rng.choice(codes)
        return HTTPStatus.OK

    def served(self, model: ModelSpec) -> tuple[int, int, int, int, int]:
        rng = self.rng
        inp, out = model.input_tokens.draw(rng), model.output_tokens.draw(rng)
        cached = 0
        if model.cached_share and inp >= CACHE_BLOCK * 8:
            share = min(max(rng.gauss(model.cached_share, CACHE_NOISE), 0), 0.99)
            cached = int(inp * share) // CACHE_BLOCK * CACHE_BLOCK
        reasoning = int(out * rng.uniform(0.2, 0.7)) if model.reasoning else 0
        return model.latency_ms.draw(rng), inp, cached, out, reasoning
