import random
import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import accumulate

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import Group
from django.core.management.base import CommandError
from django.db.models import Q

from management.demo.catalog import ModelSpec, Spread
from management.demo.traffic import BUSY_WEIGHT, THROTTLED, USER_AGENTS, KeyTraffic, SeededKeys
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

IDLE_KEY_SHARE = 0.15
SECOND_TEAM_SHARE = 0.1
TEAMS_PER_ORG_ADMIN = 100
KEYS_PER_USER = ((1, 2, 3), (6, 3, 1))

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


def seeded_users() -> Q:
    return Q(email__endswith=f"@{SEED_DOMAIN}")


def seed_exists() -> bool:
    return (
        Org.objects.filter(name__startswith=ORG_PREFIX).exists()
        or User.objects.filter(seeded_users()).exists()
    )


def delete_seeded() -> dict[str, int]:
    users = User.objects.filter(seeded_users())
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
    def __init__(self, rng: random.Random, models: Sequence[ModelSpec], start: datetime) -> None:
        self.rng = rng
        self.models = models
        self.model_weights = list(accumulate(m.weight for m in models))
        self.start = start
        self.keys = SeededKeys()
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
            self.keys.personal.append(self.key(token.pk, weight, (local_part,), 0.03))

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
            Token(user_id=users[rosters[t][0]].id, service_account=sa, name=sa.name)
            for sa, t in zip(accounts, homes, strict=True)
        )
        for token in tokens:
            end_users = tuple(
                f"u-{self.rng.getrandbits(32):08x}" for _ in range(self.rng.randint(20, 400))
            )
            weight = 10 * self.rng.paretovariate(1.3)
            self.keys.service.append(self.key(token.pk, weight, end_users, 0.7))

    def tokens(self, unsaved: Iterable[Token]) -> list[Token]:
        rows = list(unsaved)
        for row in rows:
            row.key_hash = Token._hash_key(secrets.token_urlsafe(32))
            row.key_preview = "sk-...seed"
        created = Token.objects.bulk_create(rows)
        Token.objects.filter(pk__in=[t.pk for t in created]).update(created_at=self.start)
        return created

    def key(
        self, pk: int, weight: float, user_ids: Sequence[str], user_id_share: float
    ) -> KeyTraffic:
        rng = self.rng
        return KeyTraffic(
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
        busy = sorted(self.keys.personal, key=lambda k: -k.weight)[:20]
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
