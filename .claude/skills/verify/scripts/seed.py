"""Seed a fresh verification database. Run once per instance, right after migrate.

Two orgs, one account per role, a team with a service account, personal keys, and a
week of requests whose models come from the router config the instance loads.
"""

import json
import random
from datetime import timedelta

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402
from tos.models import TermsOfService, UserAgreement  # noqa: E402

from gateway.config import get_router_config  # noqa: E402
from management.models import (  # noqa: E402
    Org,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
)

User = get_user_model()
rng = random.Random(7)

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
for day in range(7):
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
