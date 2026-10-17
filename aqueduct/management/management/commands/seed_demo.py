"""Fill the database with a production-sized demo: two orgs, their teams, users, keys, and a
month of request log. Everything it creates is under `ORG_PREFIX` orgs or `@SEED_DOMAIN` users,
so `--reset` and `--delete` remove exactly that. `seed_demo_models.datalab.yaml`, next to this
file, is the default model mix and shows the `--models-file` format."""

import math
import random
import secrets
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from itertools import accumulate
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from management.key_activity import router_models
from management.models import (
    Batch,
    FileObject,
    Org,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
    VectorStore,
)

User = get_user_model()

SEED_DOMAIN = "seed.invalid"
ORG_PREFIX = "seed-demo-"
"""Every org whose name starts with this counts as seeded, whoever created it."""

DEFAULTS: dict[str, Any] = {
    "teams": 300,
    "small_teams": 30,
    "service_accounts": 25,
    "requests_per_day": 60_000,
    "days": 30,
    "local_time_zone": "Europe/Vienna",
    "models_file": Path(__file__).with_name("seed_demo_models.datalab.yaml"),
}
"""The size of a default run. `requests_per_day` counts calls that name a model. The defaults and
the traffic shape below follow the datalab production figures of October 2026."""
SQLITE_WARN_ROWS = 500_000
INSERT_BATCH = 10_000


class Kind(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    SPEECH = "speech"
    TRANSCRIPTION = "transcription"
    IMAGE = "image"


@dataclass(frozen=True)
class Spread:
    """A log-normal draw: half the values fall below `median`, capped at `cap`."""

    median: float
    sigma: float
    cap: int = 2**31 - 1

    def draw(self, rng: random.Random) -> int:
        if self.median <= 0:
            return 0
        return min(self.cap, int(rng.lognormvariate(math.log(self.median), self.sigma)))


@dataclass(frozen=True)
class Shape:
    """How requests of one model kind look in the log, unless the model's figures say otherwise."""

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
"""The chat spreads reproduce the production totals: about 70,000 input and 1,900 output tokens
per request on average, and about 1% of outputs stopped at `MAX_OUTPUT`."""
Z90 = 1.2816
"""The 90th percentile of the standard normal distribution."""
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
"""The share of a key's calls that use its favourite model; the rest follow the overall mix."""
CACHE_BLOCK = 128
CACHE_NOISE = 0.08
RESPONSES_API_SHARE = 0.1
OTHER_AGENT_SHARE = 0.1
PROCESSING_MS = Spread(6, 0.6, 5000)
FAILED_MS = (Spread(120, 1.0, 600_000), Spread(30, 1.0, 10_000))
"""Response times of other failures and of rate-limited calls."""
FAILURES = (
    (0.006, (400, 400, 401, 404, 413, 422)),
    (0.0015, (500, 502, 503, 504)),
    (0.001, (499,)),
)
"""The share of calls that fail with each group of status codes, besides rate limits."""
THROTTLED = (0.01, 0.04)
"""The share of rate-limited calls of an ordinary key and of a busy one."""
BUSY_WEIGHT = 20
UNFINISHED_WINDOW = timedelta(seconds=90)
UNFINISHED_SHARE = 0.6
"""The share of calls younger than `UNFINISHED_WINDOW` that have no status yet."""

LOCAL_HOUR_LOAD: tuple[float, ...] = (
    *(2303, 2222, 2143, 2144, 1976, 1797, 1673, 1545, 1704, 2120, 2898, 3160),
    *(3601, 3534, 3177, 3282, 3372, 3460, 3219, 3003, 2521, 2673, 2466, 2201),
)
"""Average requests in each local hour; agents and batch jobs keep the nights busy."""
WEEKDAY_LOAD = (1, 1, 1, 1, 1, 0.8, 0.6)
"""Relative traffic from Monday to Sunday."""
DAY_NOISE = (0.75, 1.3)
SERVICE_SHARE = 0.4
"""The share of traffic from service account keys."""
PERSONAL_KEY_CAP = 0.025
"""The largest share of all traffic one personal key may have. Service accounts are uncapped."""
IDLE_KEY_SHARE = 0.15
SECOND_TEAM_SHARE = 0.1
TEAMS_PER_ORG_ADMIN = 100
SMALL_ORG_SERVICE_ACCOUNTS = 3
KEYS_PER_USER = ((1, 2, 3), (6, 3, 1))
"""Personal keys per user, and how often each count occurs."""

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
FIRST_NAMES: tuple[str, ...] = (
    "Anna",
    "Ben",
    "Clara",
    "David",
    "Elif",
    "Felix",
    "Greta",
    "Ivan",
    "Jonas",
    "Karim",
)
FIRST_NAMES += ("Lena", "Maria", "Mia", "Noah", "Omar", "Paul", "Rosa", "Sara", "Tobias", "Yusuf")
LAST_NAMES: tuple[str, ...] = (
    "Bauer",
    "Berger",
    "Fischer",
    "Gruber",
    "Hofer",
    "Huber",
    "Kaya",
    "Klein",
    "Lang",
)
LAST_NAMES += ("Mayer", "Moser", "Novak", "Pichler", "Reiter", "Steiner", "Wagner", "Weber")
TEAM_FIELDS: tuple[str, ...] = (
    "Applied NLP",
    "Vision",
    "Robotics",
    "Bioinformatics",
    "Climate",
    "Data Systems",
)
TEAM_FIELDS += ("Security", "HCI", "Theory", "Quantum", "Materials", "Linguistics", "Energy")
TEAM_FIELDS += ("Neuroscience", "Logistics", "Health AI", "Statistics", "Compilers", "Networks")
TEAM_KINDS = ("Lab", "Group", "Unit", "Studio", "Office")
KEY_NAMES = ("laptop", "notebook", "thesis", "cluster", "ci", "editor", "scripts", "playground")
SERVICE_NAMES = (
    "chat-frontend",
    "rag-indexer",
    "eval-runner",
    "slack-bot",
    "transcriber",
    "doc-summarizer",
    "moodle-plugin",
    "nightly-batch",
)


class LogRow(NamedTuple):
    """One `Request` row, in the column order of the bulk insert."""

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
class ModelSpec:
    name: str
    kind: Kind
    weight: float
    reasoning: bool
    input_tokens: Spread
    output_tokens: Spread
    latency_ms: Spread
    cached_share: float


@dataclass(frozen=True)
class Key:
    """A seeded key and the traits its requests share."""

    id: int
    weight: float
    primary_model: ModelSpec
    user_agent: str
    ip_address: str
    user_ids: Sequence[str]
    user_id_share: float
    """The share of its calls that name an end user in `user_id`."""
    throttled: float
    """The share of its calls that hit a rate limit."""


@dataclass
class People:
    """The keys of both seeded orgs, split by whether a person or a service account owns them."""

    personal: list[Key] = field(default_factory=list)
    service: list[Key] = field(default_factory=list)


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


def load_catalog(path: Path) -> list[ModelSpec]:
    try:
        specs = [model_spec(m) for m in yaml.safe_load(path.read_text())["models"]]
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        msg = (
            f"Cannot read {path}: {exc!r}. Expected `models:` as a list like "
            f"{DEFAULTS['models_file']}, kind one of {', '.join(Kind)}."
        )
        raise CommandError(msg) from exc
    if not specs:
        msg = f"{path} lists no models."
        raise CommandError(msg)
    return specs


def router_config_for(models: Sequence[ModelSpec]) -> dict[str, Any]:
    return {
        "model_list": [
            {
                "model_name": m.name,
                "litellm_params": {
                    "model": f"openai/{m.name}",
                    "api_key": "os.environ/OPENAI_API_KEY",
                },
                "model_info": {"mode": SHAPES[m.kind].mode},
            }
            for m in models
        ]
    }


def seed_users() -> Q:
    return Q(email__endswith=f"@{SEED_DOMAIN}")


def delete_seeded() -> dict[str, int]:
    """Delete the seeded orgs and users and everything that hangs off them."""
    users = User.objects.filter(seed_users())
    orgs = Org.objects.filter(name__startswith=ORG_PREFIX)
    strangers = UserProfile.objects.filter(org__in=orgs).exclude(user__in=users)
    if strangers.exists():
        emails = ", ".join(strangers.values_list("user__email", flat=True)[:20])
        msg = (
            f"Users who were not seeded belong to a {ORG_PREFIX}* org: {emails}. Move them to "
            "another org first; nothing was deleted."
        )
        raise CommandError(msg)
    tokens = Token.objects.filter(Q(user__in=users) | Q(service_account__team__org__in=orgs))
    counts = {"requests": Request.objects.filter(token__in=tokens).delete()[0]}
    counts["keys"] = tokens.delete()[1].get("management.Token", 0)
    counts["users"] = users.delete()[1].get(User._meta.label, 0)
    counts["orgs"] = orgs.delete()[1].get("management.Org", 0)
    return counts


@dataclass(frozen=True)
class SeededUser:
    id: int
    profile_id: int
    email: str


class Builder:
    """Creates the orgs, teams, users, and keys, drawing every choice from one seeded RNG."""

    def __init__(self, rng: random.Random, models: Sequence[ModelSpec], start: datetime) -> None:
        self.rng = rng
        self.models = models
        self.model_weights = list(accumulate(m.weight for m in models))
        self.start = start
        self.people = People()
        self.groups = {g.name: g.id for g in Group.objects.filter(name__in=UserGroup.values)}
        self.serial = 0

    def org(self, name: str, teams: int, service_accounts: int) -> None:
        org = Org.objects.create(name=f"{ORG_PREFIX}{name}")
        team_rows = Team.objects.bulk_create(
            Team(name=self.team_name(i), org=org, description="Seeded demo team")
            for i in range(teams)
        )
        rosters = self.rosters(len(team_rows))
        users = self.users(org, len({u for roster in rosters for u in roster}))
        admins = self.users(org, 1 + teams // TEAMS_PER_ORG_ADMIN, group=UserGroup.ORG_ADMIN)
        memberships = []
        for team, roster in zip(team_rows, rosters, strict=True):
            admins_in_team = self.rng.choice((1, 1, 2))
            memberships += [
                TeamMembership(
                    user_profile_id=users[u].profile_id, team=team, is_admin=slot < admins_in_team
                )
                for slot, u in enumerate(roster)
            ]
        TeamMembership.objects.bulk_create(memberships)
        self.personal_keys([*users, *admins])
        self.service_accounts(team_rows, rosters, users, service_accounts)

    def team_name(self, i: int) -> str:
        kind = TEAM_KINDS[i // len(TEAM_FIELDS) % len(TEAM_KINDS)]
        name = f"{TEAM_FIELDS[i % len(TEAM_FIELDS)]} {kind}"
        rounds = i // (len(TEAM_FIELDS) * len(TEAM_KINDS))
        return f"{name} {rounds + 1}" if rounds else name

    def rosters(self, teams: int) -> list[list[int]]:
        """Member indexes per team: three to five people, about one in ten also in another team."""
        rosters: list[list[int]] = []
        in_one_team: list[int] = []
        users = 0
        for _ in range(teams):
            roster: list[int] = []
            newcomers: list[int] = []
            for _ in range(self.rng.randint(3, 5)):
                if in_one_team and self.rng.random() < SECOND_TEAM_SHARE:
                    roster.append(in_one_team.pop(self.rng.randrange(len(in_one_team))))
                else:
                    newcomers.append(users)
                    users += 1
            in_one_team += newcomers
            rosters.append(newcomers + roster)
        return rosters

    def users(self, org: Org, count: int, group: str = UserGroup.USER) -> list[SeededUser]:
        rows = []
        for _ in range(count):
            first, last = self.rng.choice(FIRST_NAMES), self.rng.choice(LAST_NAMES)
            self.serial += 1
            email = f"{first}.{last}.{self.serial}@{SEED_DOMAIN}".lower()
            rows.append(
                User(
                    username=email,
                    email=email,
                    first_name=first,
                    last_name=last,
                    password=f"{UNUSABLE_PASSWORD_PREFIX}seed",
                )
            )
        created = User.objects.bulk_create(rows)
        profiles = UserProfile.objects.bulk_create(UserProfile(user=u, org=org) for u in created)
        User.groups.through.objects.bulk_create(
            User.groups.through(user_id=u.pk, group_id=self.groups[group]) for u in created
        )
        return [SeededUser(u.pk, p.pk, u.email) for u, p in zip(created, profiles, strict=True)]

    def personal_keys(self, users: Sequence[SeededUser]) -> None:
        owners = [u for u in users for _ in range(self.rng.choices(*KEYS_PER_USER)[0])]
        tokens = self.tokens(Token(user_id=u.id, name=self.rng.choice(KEY_NAMES)) for u in owners)
        for token, owner in zip(tokens, owners, strict=True):
            idle = self.rng.random() < IDLE_KEY_SHARE
            weight = 0.0 if idle else self.rng.paretovariate(1.1)
            local_part = owner.email.split("@")[0]
            self.people.personal.append(self.key(token.pk, weight, (local_part,), 0.03))

    def service_accounts(
        self,
        teams: Sequence[Team],
        rosters: Sequence[Sequence[int]],
        users: Sequence[SeededUser],
        count: int,
    ) -> None:
        homes = self.rng.sample(range(len(teams)), min(count, len(teams)))
        homes += [self.rng.randrange(len(teams)) for _ in range(count - len(homes))]
        accounts = ServiceAccount.objects.bulk_create(
            ServiceAccount(
                name=f"{SERVICE_NAMES[i % len(SERVICE_NAMES)]}-{i + 1}",
                team=teams[t],
                description="Seeded service account",
            )
            for i, t in enumerate(homes)
        )
        tokens = self.tokens(
            Token(user_id=users[rosters[t][0]].id, service_account=sa, name=f"{sa.name} key")
            for sa, t in zip(accounts, homes, strict=True)
        )
        for token in tokens:
            end_users = tuple(
                f"u-{self.rng.getrandbits(32):08x}" for _ in range(self.rng.randint(20, 400))
            )
            weight = 10 * self.rng.paretovariate(1.3)
            self.people.service.append(self.key(token.pk, weight, end_users, 0.7))

    def tokens(self, unsaved: Iterable[Token]) -> list[Token]:
        rows = list(unsaved)
        for row in rows:
            row.key_hash = Token._hash_key(secrets.token_urlsafe(32))
            row.key_preview = "sk-...seed"
        created = Token.objects.bulk_create(rows)
        Token.objects.filter(pk__in=[t.pk for t in created]).update(created_at=self.start)
        return created

    def key(self, pk: int, weight: float, user_ids: Sequence[str], user_id_share: float) -> Key:
        rng = self.rng
        return Key(
            id=pk,
            weight=weight,
            primary_model=rng.choices(self.models, cum_weights=self.model_weights)[0],
            user_agent=rng.choice(USER_AGENTS),
            ip_address=f"10.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}",
            user_ids=user_ids,
            user_id_share=user_id_share,
            throttled=THROTTLED[weight > BUSY_WEIGHT],
        )

    def storage(self, now: datetime) -> dict[str, int]:
        """A few files, batches, and vector stores of busy personal keys, so their pages have
        rows."""
        busy = sorted(self.people.personal, key=lambda k: -k.weight)[:20]
        start, end = int(self.start.timestamp()), int(now.timestamp())
        rng = self.rng
        files = FileObject.objects.bulk_create(
            FileObject(
                id=f"file-{rng.getrandbits(96):024x}",
                token_id=rng.choice(busy).id,
                filename=f"{rng.choice(('batch_input', 'train', 'corpus', 'notes'))}_{i}.jsonl",
                purpose="batch" if i % 2 else rng.choice(("user_data", "assistants", "fine-tune")),
                bytes=Spread(400_000, 1.5, 512_000_000).draw(rng),
                created_at=rng.randint(start, end),
            )
            for i in range(40)
        )
        statuses = ("completed", "completed", "completed", "in_progress", "failed", "cancelled")
        batches = Batch.objects.bulk_create(
            Batch(
                id=f"batch_{rng.getrandbits(96):024x}",
                token_id=f.token_id,
                input_file=f,
                endpoint="/v1/chat/completions",
                completion_window="24h",
                status=(status := rng.choice(statuses)),
                created_at=f.created_at,
                completed_at=f.created_at + rng.randint(600, 80_000)
                if status == "completed"
                else None,
                request_counts={"total": (n := rng.randint(10, 5000)), "completed": n, "failed": 0},
            )
            for f in files
            if f.purpose == "batch"
        )
        stores = VectorStore.objects.bulk_create(
            VectorStore(
                id=f"vs_{rng.getrandbits(96):024x}",
                token_id=rng.choice(busy).id,
                name=f"{rng.choice(('papers', 'handbook', 'course', 'wiki'))} {i}",
                status="completed",
                usage_bytes=Spread(2_000_000, 1.2).draw(rng),
                created_at=(created := rng.randint(start, end)),
                last_active_at=rng.randint(created, end),
            )
            for i in range(10)
        )
        return {"files": len(files), "batches": len(batches), "vector stores": len(stores)}


def capped(weights: list[float], share: float) -> list[float]:
    """Lower the largest `weights` until none exceeds `share` of their sum, if that is
    possible."""
    if share * len(weights) <= 1:
        return weights
    for _ in range(100):
        cap = share * sum(weights)
        if max(weights) <= cap * 1.001:
            break
        weights = [min(w, cap) for w in weights]
    return weights


class Traffic:
    """Draws the request log: how many calls each hour gets, which key makes each, and what the
    log row for it says."""

    def __init__(
        self, rng: random.Random, models: Sequence[ModelSpec], now: datetime, zone: ZoneInfo
    ) -> None:
        self.rng = rng
        self.models = models
        self.model_weights = list(accumulate(m.weight for m in models))
        self.now = now
        self.zone = zone

    def hours(self, start: datetime) -> list[tuple[datetime, float, float]]:
        """Each hour slot from `start` to now, with its length in seconds and relative load."""
        slots = []
        day_noise: dict[Any, float] = {}
        slot = start
        while slot < self.now:
            end = min(
                slot.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), self.now
            )
            local = slot.astimezone(self.zone)
            load = LOCAL_HOUR_LOAD[local.hour] * WEEKDAY_LOAD[local.weekday()]
            noise = day_noise.setdefault(local.date(), self.rng.uniform(*DAY_NOISE))
            seconds = (end - slot).total_seconds()
            slots.append((slot, seconds, load * noise * seconds / 3600))
            slot = end
        return slots

    def rows(
        self,
        keys: Sequence[Key],
        total: int,
        slots: Sequence[tuple[datetime, float, float]],
        key_cap: float,
    ) -> Iterator[LogRow]:
        """`total` calls by `keys` spread over `slots`, none of the keys making more than
        `key_cap` of them."""
        cum = list(accumulate(capped([k.weight for k in keys], key_cap)))
        if not keys or cum[-1] <= 0:
            return
        scale = total / sum(load for *_, load in slots)
        owed = 0.0
        for slot, seconds, load in slots:
            owed += load * scale
            count = round(owed)
            owed -= count
            for key in self.rng.choices(keys, cum_weights=cum, k=count):
                yield self.row(key, slot + timedelta(seconds=self.rng.random() * seconds))

    def row(self, key: Key, at: datetime) -> LogRow:
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

    def status(self, key: Key, at: datetime) -> int | None:
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
        """Response time and token counts of a successful call: (latency, input, cached, output,
        reasoning). The gateway logs a streamed call's time to its last chunk, so streaming
        changes nothing in the log."""
        rng = self.rng
        inp, out = model.input_tokens.draw(rng), model.output_tokens.draw(rng)
        cached = 0
        if model.cached_share and inp >= CACHE_BLOCK * 8:
            share = min(max(rng.gauss(model.cached_share, CACHE_NOISE), 0), 0.99)
            cached = int(inp * share) // CACHE_BLOCK * CACHE_BLOCK
        reasoning = int(out * rng.uniform(0.2, 0.7)) if model.reasoning else 0
        return model.latency_ms.draw(rng), inp, cached, out, reasoning


class Command(BaseCommand):
    help = (
        "Seed a production-sized demo: a large and a small org with teams, users, keys, service "
        "accounts, and a request log. Development only."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.set_defaults(**DEFAULTS)
        parser.add_argument("--teams", type=int, help="Teams in the large org.")
        parser.add_argument("--small-teams", type=int, help="Teams in the small org.")
        parser.add_argument(
            "--service-accounts", type=int, help="Service accounts in the large org."
        )
        parser.add_argument("--requests-per-day", type=int)
        parser.add_argument("--days", type=int, help="Days of log, ending now.")
        parser.add_argument(
            "--local-time-zone",
            help="The zone whose working hours shape the traffic; TIME_ZONE is left alone.",
        )
        parser.add_argument("--seed", type=int, default=0, help="Seed of every random choice.")
        parser.add_argument(
            "--models-file",
            type=Path,
            help="YAML or JSON model mix. Defaults to seed_demo_models.datalab.yaml.",
        )
        parser.add_argument(
            "--write-router-config",
            type=Path,
            metavar="PATH",
            help="Write a router config serving the models of --models-file to PATH and stop.",
        )
        parser.add_argument(
            "--reset", action="store_true", help="Delete earlier seeded data, then seed."
        )
        parser.add_argument(
            "--delete", action="store_true", help="Delete earlier seeded data and stop."
        )
        parser.add_argument(
            "--force", action="store_true", help="Run even when DEBUG is off. Use with care."
        )

    def handle(self, *args: object, **options: Any) -> None:
        if options["write_router_config"]:
            self.write_router_config(
                load_catalog(options["models_file"]), options["write_router_config"]
            )
            return
        self.check_target(force=options["force"])
        if options["delete"]:
            with transaction.atomic():
                self.delete()
        else:
            models = self.checked(load_catalog(options["models_file"]))
            zone = self.zone(options["local_time_zone"])
            with transaction.atomic():
                if options["reset"]:
                    self.delete()
                if (
                    Org.objects.filter(name__startswith=ORG_PREFIX).exists()
                    or User.objects.filter(seed_users()).exists()
                ):
                    msg = "Seeded data already exists. Pass --reset to replace it or --delete."
                    raise CommandError(msg)
                self.seed(models, zone, options)
        call_command("rebuild_usage_rollups", stdout=self.stdout)

    def check_target(self, *, force: bool) -> None:
        if settings.DEBUG:
            return
        if not force:
            msg = (
                "seed_demo writes demo data and runs only with DEBUG on. Pass --force to override."
            )
            raise CommandError(msg)
        name = connection.settings_dict["NAME"]
        self.stderr.write(f"DEBUG is off. Writing to the {connection.vendor} database {name}.")

    def delete(self) -> None:
        started = time.monotonic()
        counts = delete_seeded()
        self.stdout.write(f"Deleted {self.summary(counts)} in {time.monotonic() - started:.1f} s")

    @staticmethod
    def zone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"Unknown --local-time-zone {name!r}."
            raise CommandError(msg) from exc

    def write_router_config(self, models: list[ModelSpec], path: Path) -> None:
        path.write_text(yaml.safe_dump(router_config_for(models), sort_keys=False))
        self.stdout.write(
            f"Wrote {path}. Point LITELLM_ROUTER_CONFIG_FILE_PATH at it, then run seed_demo again."
        )

    def checked(self, models: list[ModelSpec]) -> list[ModelSpec]:
        missing = sorted({m.name for m in models} - set(router_models()))
        if missing:
            msg = (
                f"Not in the router config ({settings.LITELLM_ROUTER_CONFIG_FILE_PATH}): "
                f"{', '.join(missing)}. Requests may only name served models. Pass "
                "--write-router-config <path> to write a router config that serves them."
            )
            raise CommandError(msg)
        return models

    def seed(self, models: list[ModelSpec], zone: ZoneInfo, options: dict[str, Any]) -> None:
        rng = random.Random(options["seed"])  # noqa: S311
        now = timezone.now()
        start = now - timedelta(days=options["days"])
        total = round(options["requests_per_day"] * options["days"] / (1 - MODELLESS_SHARE))
        if connection.vendor == "sqlite" and total > SQLITE_WARN_ROWS:
            self.stderr.write(
                f"{total:,} requests on SQLite will take a while. Postgres is faster."
            )
        started = time.monotonic()
        builder = Builder(rng, models, start - timedelta(days=7))
        cap = settings.MAX_SERVICE_ACCOUNTS_PER_TEAM
        builder.org(
            "campus", options["teams"], min(options["service_accounts"], cap * options["teams"])
        )
        small_accounts = min(SMALL_ORG_SERVICE_ACCOUNTS, cap * options["small_teams"])
        builder.org("institute", options["small_teams"], small_accounts)
        people = builder.people
        self.stdout.write(
            f"Created 2 orgs, {builder.serial:,} users, {len(people.personal):,} personal "
            f"keys, {len(people.service)} service account keys, "
            f"{self.summary(builder.storage(now))} in {time.monotonic() - started:.1f} s"
        )
        traffic = Traffic(rng, models, now, zone)
        slots = traffic.hours(start)
        service = round(total * SERVICE_SHARE) if people.service else 0
        personal = total - service
        personal_cap = PERSONAL_KEY_CAP * total / max(personal, 1)
        rows = (
            row
            for keys, count, key_cap in (
                (people.personal, personal, personal_cap),
                (people.service, service, 1.0),
            )
            for row in traffic.rows(keys, count, slots, key_cap)
        )
        written = self.insert(rows, total)
        self.stdout.write(f"Wrote {written:,} requests in {time.monotonic() - started:.1f} s")

    def insert(self, rows: Iterator[LogRow], expected: int) -> int:
        """Write `rows` with COPY on Postgres and batched INSERTs elsewhere, which is many times
        faster than `bulk_create` of model instances."""
        table = Request._meta.db_table
        columns = ", ".join(LogRow._fields)
        written = 0
        started = time.monotonic()
        step = max(expected // 20, INSERT_BATCH)

        def progress() -> None:
            rate = written / max(time.monotonic() - started, 1e-6)
            self.stdout.write(f"  {written:,} / ~{expected:,} requests, {rate:,.0f}/s")

        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                with cursor.cursor.copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
                        written += 1
                        if written % step == 0:
                            progress()
                cursor.execute(f"ANALYZE {table}")
                return written
            ops = connection.ops
            values = ", ".join(["%s"] * len(LogRow._fields))
            sql = f"INSERT INTO {table} ({columns}) VALUES ({values})"  # noqa: S608
            batch: list[tuple[Any, ...]] = []
            for row in rows:
                batch.append((*row[:2], ops.adapt_datetimefield_value(row[2]), *row[3:]))
                written += 1
                if len(batch) == INSERT_BATCH:
                    cursor.executemany(sql, batch)
                    batch.clear()
                if written % step == 0:
                    progress()
            cursor.executemany(sql, batch)
            return written

    @staticmethod
    def summary(counts: dict[str, int]) -> str:
        return ", ".join(f"{n:,} {name}" for name, n in counts.items())
