"""Seed a fresh verification database. Run once per instance, right after migrate.

Two orgs, one account per role, a team with a service account, personal keys,
AQV_SEED_DAYS days of requests (default 7) whose models come from the router config
the instance loads, and files, batches, and vector stores for `user` and for the
team's service account.
"""

import json
import os
import random
from datetime import timedelta

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402
from tos.models import TermsOfService, UserAgreement  # noqa: E402

from gateway.config import get_router_config  # noqa: E402
from management.models import (  # noqa: E402
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
    VectorStoreFile,
)

User = get_user_model()
rng = random.Random(7)
DAYS = int(os.environ.get("AQV_SEED_DAYS", "7"))

home = Org.objects.create(name="Verify Org")
other = Org.objects.create(name="Other Org")
terms = TermsOfService.objects.create(active=True, content="Verification terms of service.")

ACCOUNTS = [
    ("admin", UserGroup.ADMIN, home),
    ("org-admin", UserGroup.ORG_ADMIN, home),
    ("team-admin", UserGroup.USER, home),
    ("team-member", UserGroup.USER, home),
    ("user", UserGroup.USER, home),
    ("other-org-admin", UserGroup.ORG_ADMIN, other),
    ("new-user", UserGroup.USER, home),
]
users = {}
for username, group, org in ACCOUNTS:
    is_admin = group == UserGroup.ADMIN
    user = User.objects.create_user(
        username=username,
        email=f"{username}@verify.invalid",
        is_staff=is_admin,
        is_superuser=is_admin,
    )
    UserProfile.objects.create(user=user, org=org).group = group
    if username != "new-user":
        UserAgreement.objects.create(terms_of_service=terms, user=user)
    users[username] = user

team = Team.objects.create(name="Verify Team", org=home, description="Seeded for verification")
TeamMembership.objects.create(team=team, user_profile=users["team-admin"].profile, is_admin=True)
TeamMembership.objects.create(team=team, user_profile=users["team-member"].profile)
Team.objects.create(name="Other Team", org=other, description="Seeded in the other org")


def make_key(name: str, owner: User, service_account: ServiceAccount | None = None) -> Token:
    token = Token(name=name, user=owner, service_account=service_account)
    token._set_new_key()
    token.save()
    return token


service_account = ServiceAccount.objects.create(
    name="verify-pipeline", team=team, description="Seeded service account"
)
keys = [
    make_key("user-laptop", users["user"]),
    make_key("member-notebook", users["team-member"]),
    make_key("team-admin-cli", users["team-admin"]),
    make_key("org-admin-scripts", users["org-admin"]),
    make_key("verify-pipeline", users["team-admin"], service_account),
]

PATHS = {
    "embedding": "/embeddings",
    "audio_transcriptions": "/audio/transcriptions",
    "audio_speech": "/audio/speech",
    "image_generations": "/images/generations",
}
models = [
    (entry["model_name"], (entry.get("model_info") or {}).get("mode", "chat"))
    for entry in get_router_config().get("model_list", [])
]
now = timezone.now()
rows = []
for day in range(DAYS):
    for _ in range(40):
        token = rng.choice(keys)
        model, mode = rng.choice(models)
        status = rng.choices([200, 429, 400, 500], weights=[92, 4, 2, 2])[0]
        answered = status == 200
        input_tokens = rng.randint(200, 6000) if answered else 0
        rows.append(
            Request(
                token=token,
                model=model,
                timestamp=now - timedelta(days=day, minutes=rng.randint(0, 1439)),
                method="POST",
                status_code=status,
                response_time_ms=rng.randint(300, 9000) if answered else rng.randint(2, 40),
                processing_time_ms=rng.randint(5, 25),
                user_agent=rng.choice(["openai-python/1.99.0", "curl/8.5.0"]),
                path=PATHS.get(mode, "/chat/completions"),
                input_tokens=input_tokens,
                cached_input_tokens=input_tokens // rng.choice([2, 4, 10**9]),
                output_tokens=rng.randint(50, 900) if answered and mode == "chat" else 0,
                user_id=token.user.email,
            )
        )
Request.objects.bulk_create(rows)

user_key, pipeline_key = keys[0], keys[4]
stamp = int(now.timestamp())
expires = stamp + 30 * 86_400
PREVIEW = '{"custom_id": "q1", "method": "POST", "url": "/v1/chat/completions"}'


def make_file(file_id: str, token: Token, filename: str, purpose: str, size: int) -> FileObject:
    return FileObject.objects.create(
        id=file_id,
        token=token,
        filename=filename,
        purpose=purpose,
        bytes=size,
        created_at=stamp - 3600,
        expires_at=expires if purpose.startswith("batch") else None,
        preview=PREVIEW if purpose == "batch" else "",
    )


def make_batch(batch_id: str, token: Token, input_file: FileObject, status: str, **fields) -> Batch:
    return Batch.objects.create(
        id=batch_id,
        token=token,
        input_file=input_file,
        completion_window="24h",
        created_at=stamp - 1800,
        expires_at=stamp + 86_400,
        endpoint="/v1/chat/completions",
        status=status,
        **fields,
    )


prompts = make_file("file-user-prompts", user_key, "eval-prompts.jsonl", "batch", 4_800_000)
results = make_file("file-user-results", user_key, "eval-results.jsonl", "batch_output", 6_100_000)
corpus = make_file("file-user-corpus", user_key, "thesis-corpus.pdf", "user_data", 7_600_000)
team_input = make_file("file-team-input", pipeline_key, "nightly.jsonl", "batch", 900_000)
make_batch(
    "batch-user-running",
    user_key,
    prompts,
    "in_progress",
    request_counts={"total": 4000, "completed": 1228, "failed": 12},
)
make_batch(
    "batch-user-done",
    user_key,
    prompts,
    "completed",
    output_file=results,
    request_counts={"total": 500, "completed": 500, "failed": 0},
)
make_batch(
    "batch-team-running",
    pipeline_key,
    team_input,
    "in_progress",
    request_counts={"total": 200, "completed": 50, "failed": 0},
)
thesis = VectorStore.objects.create(
    id="vs_user_thesis", token=user_key, name="thesis-corpus", status="completed", created_at=stamp
)
VectorStoreFile.objects.create(
    id="vsf-user-corpus",
    vector_store=thesis,
    file_obj=corpus,
    status="completed",
    created_at=stamp,
    usage_bytes=7_600_000,
)
VectorStore.objects.create(
    id="vs_team_papers",
    token=pipeline_key,
    name="group-papers",
    status="in_progress",
    created_at=stamp,
    expires_after={"anchor": "last_active_at", "days": 14},
    last_active_at=stamp,
)

print(
    json.dumps(
        {
            "accounts": [username for username, _, _ in ACCOUNTS],
            "team": team.name,
            "team_id": team.id,
            "keys": [key.name for key in keys],
            "requests": len(rows),
            "models": sorted({model for model, _ in models}),
        }
    )
)
