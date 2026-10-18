import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http import HTTPStatus
from itertools import accumulate, chain
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from management.demo.catalog import API_PATHS, Kind, ModelSpec, Spread

RESPONSES_API_PATH = "/v1/responses"
CAP_ROUNDS = 100
CAP_TOLERANCE = 1.001


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    weight: float


@dataclass(frozen=True)
class Failure:
    share: float
    codes: Sequence[int]


@dataclass(frozen=True)
class TrafficProfile:
    local_hour_load: Sequence[float]
    weekday_load: Sequence[float]
    day_noise: tuple[float, float]
    service_share: float
    personal_key_cap: float
    idle_key_share: float
    personal_weight_alpha: float
    personal_user_id_share: float
    service_weight_scale: float
    service_weight_alpha: float
    service_end_users: tuple[int, int]
    service_user_id_share: float
    throttled_share: float
    busy_throttled_share: float
    busy_weight: float
    primary_model_share: float
    responses_api_share: float
    other_agent_share: float
    processing_ms: Spread
    reasoning_share: tuple[float, float]
    cache_block_tokens: int
    cache_min_blocks: int
    cache_noise: float
    max_cached_share: float
    unfinished_window_s: float
    unfinished_share: float
    failures: Sequence[Failure]
    failed_ms: Spread
    throttled_ms: Spread
    modelless_share: float
    modelless_not_found: float
    modelless_ms: Spread
    modelless_endpoints: Sequence[Endpoint]
    user_agents: Sequence[str]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "TrafficProfile":
        spreads = ("processing_ms", "failed_ms", "throttled_ms", "modelless_ms")
        return cls(
            **raw
            | {name: Spread(**raw[name]) for name in spreads}
            | {
                "failures": [Failure(**f) for f in raw["failures"]],
                "modelless_endpoints": [Endpoint(**e) for e in raw["modelless_endpoints"]],
            }
        )

    def log_size(self, requests_per_day: int, days: int) -> int:
        return round(requests_per_day * days / (1 - self.modelless_share))


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


def capped(weights: list[float], share: float) -> list[float]:
    if share * len(weights) <= 1:
        return weights
    for _ in range(CAP_ROUNDS):
        cap = share * sum(weights)
        if max(weights) <= cap * CAP_TOLERANCE:
            break
        weights = [min(w, cap) for w in weights]
    return weights


class Traffic:
    def __init__(
        self,
        profile: TrafficProfile,
        rng: random.Random,
        models: Sequence[ModelSpec],
        now: datetime,
        zone: ZoneInfo,
    ) -> None:
        self.profile = profile
        self.rng = rng
        self.modelless_weights = list(accumulate(e.weight for e in profile.modelless_endpoints))
        self.models = models
        self.model_weights = list(accumulate(m.weight for m in models))
        self.now = now
        self.zone = zone

    def log(self, keys: SeededKeys, start: datetime, total: int) -> Iterator[LogRow]:
        slots = self.hours(start)
        service = round(total * self.profile.service_share) if keys.service else 0
        personal = total - service
        personal_cap = self.profile.personal_key_cap * total / max(personal, 1)
        return chain(
            self.rows(keys.personal, personal, slots, personal_cap),
            self.rows(keys.service, service, slots, 1.0),
        )

    def hours(self, start: datetime) -> list[Slot]:
        p = self.profile
        slots = []
        day_noise: dict[date, float] = {}
        slot = start
        while slot < self.now:
            end = min(
                slot.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), self.now
            )
            local = slot.astimezone(self.zone)
            load = p.local_hour_load[local.hour] * p.weekday_load[local.weekday()]
            noise = day_noise.setdefault(local.date(), self.rng.uniform(*p.day_noise))
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
        rng, p = self.rng, self.profile
        agent = rng.choice(p.user_agents) if rng.random() < p.other_agent_share else key.user_agent
        user_id = rng.choice(key.user_ids) if rng.random() < key.user_id_share else ""
        common = (agent, key.ip_address)
        processing: int | None = p.processing_ms.draw(rng)
        if rng.random() < p.modelless_share:
            endpoint = rng.choices(p.modelless_endpoints, cum_weights=self.modelless_weights)[0]
            found = rng.random() >= p.modelless_not_found
            return LogRow(
                key.id,
                "",
                at,
                endpoint.method,
                HTTPStatus.OK if found else HTTPStatus.NOT_FOUND,
                p.modelless_ms.draw(rng),
                processing,
                *common,
                endpoint.path,
                user_id,
            )
        model = key.primary_model
        if rng.random() > p.primary_model_share:
            model = rng.choices(self.models, cum_weights=self.model_weights)[0]
        path = API_PATHS[model.kind]
        if model.kind is Kind.CHAT and rng.random() < p.responses_api_share:
            path = RESPONSES_API_PATH
        status = self.status(key, at)
        latency: int | None
        usage: list[int] = []
        if status is None:
            latency = processing = None
        elif status >= HTTPStatus.BAD_REQUEST:
            throttled = status == HTTPStatus.TOO_MANY_REQUESTS
            latency = (p.throttled_ms if throttled else p.failed_ms).draw(rng)
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
        p = self.profile
        unfinished = self.now - at < timedelta(seconds=p.unfinished_window_s)
        if unfinished and self.rng.random() < p.unfinished_share:
            return None
        r = self.rng.random() - key.throttled
        if r < 0:
            return HTTPStatus.TOO_MANY_REQUESTS
        for failure in p.failures:
            r -= failure.share
            if r < 0:
                return self.rng.choice(failure.codes)
        return HTTPStatus.OK

    def served(self, model: ModelSpec) -> tuple[int, int, int, int, int]:
        rng, p = self.rng, self.profile
        block = p.cache_block_tokens
        inp, out = model.input_tokens.draw(rng), model.output_tokens.draw(rng)
        cached = 0
        if model.cached_share and inp >= block * p.cache_min_blocks:
            share = min(max(rng.gauss(model.cached_share, p.cache_noise), 0), p.max_cached_share)
            cached = int(inp * share) // block * block
        reasoning = int(out * rng.uniform(*p.reasoning_share)) if model.reasoning else 0
        return model.latency_ms.draw(rng), inp, cached, out, reasoning
