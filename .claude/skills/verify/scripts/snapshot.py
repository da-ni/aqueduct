"""Render every management page as every seeded role, one text file per page.

The database is fresh, the seed and the clock are fixed, and each file holds the page's
status, redirect target, visible text of <main>, and its links. Two snapshots of the same
code are identical, so `diff -r` between two commits' snapshots lists every visible change.
"""

import contextlib
import io
import logging
import os
import random
import re
import runpy
import secrets
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

SCRIPTS = Path(__file__).resolve().parent
REPO = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else SCRIPTS.parents[3]
FROZEN = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
ROLES = {
    "user": ("user", False),
    "team-member": ("team-member", False),
    "team-admin": ("team-admin", False),
    "org-admin": ("org-admin", False),
    "admin": ("admin", False),
    "admin@all": ("admin", True),
}
GERMAN_ROLES = ("user", "org-admin")
GERMAN_PAGES = {"usage", "usage_keys", "requests", "tokens", "org", "organization", "models"}

os.environ.update(
    DJANGO_SETTINGS_MODULE="aqueduct.settings",
    DATABASE_ENGINE="django.db.backends.sqlite3",
    SILKY_ENABLED="False",
    AQV_SEED_DAYS="30",
    LITELLM_ROUTER_CONFIG_FILE_PATH=str(REPO / "example_router_config.yaml"),
    MCP_CONFIG_FILE_PATH=str(REPO / "test-mcp.json"),
)
sys.path.insert(0, str(REPO / "aqueduct"))

import django  # noqa: E402
import django.utils.timezone  # noqa: E402

real_now = django.utils.timezone.now
django.utils.timezone.now = lambda: FROZEN
random_bytes = random.Random(0)
secrets.token_urlsafe = lambda nbytes=32: random_bytes.randbytes(nbytes or 32).hex()
secrets.token_hex = lambda nbytes=32: random_bytes.randbytes(nbytes or 32).hex()

from django.conf import settings  # noqa: E402

database_folder = tempfile.TemporaryDirectory(prefix="aqv-snapshot-")
settings.DATABASES["default"]["NAME"] = str(Path(database_folder.name) / "db")
settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
settings.STORAGES = {
    **settings.STORAGES,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.urls import reverse  # noqa: E402

from management.models import Org, Request, ServiceAccount, Team, Token, VectorStore  # noqa: E402


def assert_clock_frozen() -> None:
    leaks = [
        f"{name}.{attr}"
        for name, module in list(sys.modules.items())
        for attr in ("now", "timezone_now")
        if getattr(module, attr, None) is real_now
    ]
    if leaks:
        sys.exit(f"snapshot: unfrozen clock imported as {', '.join(leaks)}")


def seed() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        runpy.run_path(str(SCRIPTS / "seed.py"))
    other_admin = get_user_model().objects.get(username="other-org-admin")
    other_key = Token(name="other-org-scripts", user=other_admin)
    other_key._set_new_key()
    other_key.save()
    user_key = Token.objects.get(name="user-laptop")
    rows = [
        Request(
            token=other_key,
            model="gpt-4.1-nano",
            timestamp=FROZEN - timedelta(hours=5 * n + 1),
            status_code=200 if n % 4 else 500,
            path="/chat/completions",
            input_tokens=100 * n,
            output_tokens=10 * n,
        )
        for n in range(30)
    ]
    rows += [
        Request(token=user_key, model="gpt-4.1-nano", timestamp=FROZEN - timedelta(minutes=m))
        for m in (2, 9, 30)
    ]
    rows.append(
        Request(
            token=user_key,
            model="retired-model",
            timestamp=FROZEN - timedelta(hours=3),
            status_code=200,
            path="/chat/completions",
            input_tokens=500,
            output_tokens=50,
        )
    )
    Request.objects.bulk_create(rows)


def url(name: str, *args: object, **query: object) -> str:
    path = reverse(name, args=args)
    return f"{path}?{urlencode(query)}" if query else path


def overview_pages(key_ids: list[int]) -> Iterator[tuple[str, str]]:
    for span in ("1h", "1d", "1w", "1m"):
        yield "usage", url("usage", span=span)
    yield "usage", url("usage")
    yield "usage", url("usage", metric="requests", span="1w")
    yield "usage", url("usage", model="gpt-4.1-nano", span="1w")
    yield "usage", url("usage", heat_model="gpt-4.1-nano", span="1m")
    yield "usage", url("usage", scope="mine", span="1w")
    for key_id in key_ids:
        yield "usage", url("usage", key=key_id, span="1m")
    yield "usage_keys", url("usage_keys")
    for sort in ("tokens", "requests", "name"):
        yield "usage_keys", url("usage_keys", sort=sort, span="1m")
    yield "usage_keys", url("usage_keys", q="pipeline", span="1m")
    yield "usage_keys", url("usage_keys", page=2)
    yield "usage_keys", url("usage_keys", span="1h")


PAGER_ROOTS: list[str] = []


def pages(ids: dict[str, list[int] | list[str]]) -> Iterator[tuple[str, str]]:
    yield from overview_pages(ids["keys"][:2])
    yield "requests", url("requests")
    yield from (("requests", root) for root in PAGER_ROOTS)
    for status in ("failed", "2xx", "429", "4xx", "5xx"):
        yield "requests", url("requests", status=status, span="1m")
    for sort in ("time", "-model", "status", "response"):
        yield "requests", url("requests", sort=sort, span="1m")
    yield "requests", url("requests", q="curl", span="1m")
    yield "requests", url("requests", key=ids["keys"][1], span="1m")
    yield "requests", url("requests", model="whisper-1", span="1m")
    yield "requests", url("requests", scope="mine", span="1w")
    yield "requests", url("requests", key_q="pipe")
    for request_id in ids["requests"]:
        yield "requests", url("requests", request=request_id, span="1m")
    yield "tokens", url("tokens")
    yield "tokens", url("tokens", kind="service")
    yield "tokens", url("tokens", q="laptop")
    yield "tokens", url("token_create")
    for key_id in ids["keys"]:
        yield "tokens", url("token_detail", key_id)
        yield "tokens", url("token_detail", key_id, metric="requests")
    for key_id in ids["keys"][:2]:
        yield "tokens", url("token_edit", key_id)
        yield "tokens", url("token_delete", key_id)
    yield "org", url("org")
    yield "org", url("org", q="verify")
    yield "org", url("team_create")
    for team_id in ids["teams"]:
        yield "org", url("team", team_id)
        yield "org", url("team_edit", team_id)
        yield "org", url("team_delete", team_id)
        yield "org", url("team_availability", team_id)
        yield "org", url("team_admins_manage", team_id)
        yield "org", url("service_account_create", team_id)
    for account_id in ids["service_accounts"]:
        yield "org", url("service_account_edit", account_id)
        yield "org", url("service_account_delete", account_id)
        yield "org", url("service_account_transfer", account_id)
    yield "organization", url("organization")
    for org_id in ids["orgs"]:
        yield "organization", url("organization_limits", org_id)
        yield "organization", url("organization_availability", org_id)
    yield "models", url("models")
    yield "mcp_servers", url("mcp_servers")
    yield "files", url("files")
    yield "files", url("files", file="file-user-prompts")
    yield "batches", url("batches")
    yield "vector_stores", url("vector_stores")
    for store_id in ids["vector_stores"]:
        yield "vector_stores", url("vector_store_detail", store_id)
    yield "settings", url("settings")
    yield "privacy", url("privacy")


class MainText(HTMLParser):
    HIDDEN = frozenset({"script", "style", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inside = 0
        self.hidden = 0
        self.found = False
        self.lines: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self.inside += 1
            self.found = True
        elif self.inside and tag in self.HIDDEN:
            self.hidden += 1
        if self.inside:
            target = dict(attrs).get({"a": "href", "form": "action"}.get(tag, ""))
            if target is not None:
                self.links.append(f"{tag} {target}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self.inside -= 1
        elif self.inside and tag in self.HIDDEN:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self.inside and not self.hidden and text:
            self.lines.append(text)


def render(client: Client, path: str) -> tuple[str, list[str]]:
    cache.clear()
    response = client.get(path)
    parser = MainText()
    if response.get("Content-Type", "").startswith("text/html"):
        parser.feed(response.content.decode())
    body = parser.lines if parser.found else ["(no <main>)"]
    page = "\n".join(
        [
            f"GET {path}",
            f"status {response.status_code}",
            f"location {response.get('Location', '-')}",
            "",
            *body,
            "",
            "links:",
            *parser.links,
            "",
        ]
    )
    return page, [link.removeprefix("a ") for link in parser.links if link.startswith("a ")]


def file_name(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9=._-]+", "_", path.removeprefix("/aqueduct/management/")) + ".txt"


def pager_links(links: list[str], path: str, marker: str) -> list[str]:
    base = path.split("?", maxsplit=1)[0]
    found = [base + link if link.startswith("?") else link for link in links if marker in link]
    return list(dict.fromkeys(found))[:1]


def snapshot_role(out: Path, role: str, ids: dict, *, lang: str, only: set[str] | None) -> int:
    username, all_orgs = ROLES[role]
    client = Client()
    client.force_login(get_user_model().objects.get(username=username))
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
    if all_orgs:
        session = client.session
        session["org"] = "all"
        session.save()
    folder = out / lang / role
    folder.mkdir(parents=True, exist_ok=True)
    written = 0
    for group, path in pages(ids):
        if only is not None and group not in only:
            continue
        queue = [path]
        while queue:
            current = queue.pop(0)
            page, links = render(client, current)
            (folder / file_name(current)).write_text(page)
            written += 1
            if current in PAGER_ROOTS:
                for marker in ("before=", "page=last", "page=2"):
                    queue += pager_links(links, current, marker)
            elif "before=" in current:
                queue += pager_links(links, current, "after=")
    return written


def main() -> None:
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: snapshot.py <out-dir> [<checkout>]")
    out = Path(sys.argv[1])
    if out.exists() and any(out.iterdir()):
        sys.exit(f"snapshot: {out} is not empty")
    setup_test_environment()
    logging.disable(logging.WARNING)
    PAGER_ROOTS.extend([url("requests", span="1m"), url("requests", sort="-tokens", span="1m")])
    call_command("migrate", verbosity=0)
    seed()
    assert_clock_frozen()
    user_key = Token.objects.get(name="user-laptop")
    pipeline_key = Token.objects.get(name="verify-pipeline")
    ids = {
        "keys": [
            user_key.id,
            pipeline_key.id,
            *Token.objects.exclude(id__in=[user_key.id, pipeline_key.id])
            .order_by("id")
            .values_list("id", flat=True),
        ],
        "requests": [
            Request.objects.filter(token=key).latest("timestamp").id
            for key in (user_key, pipeline_key)
        ],
        "teams": list(Team.objects.order_by("id").values_list("id", flat=True)),
        "service_accounts": list(
            ServiceAccount.objects.order_by("id").values_list("id", flat=True)
        ),
        "orgs": list(Org.objects.order_by("id").values_list("id", flat=True)),
        "vector_stores": list(VectorStore.objects.order_by("id").values_list("id", flat=True)),
    }
    written = 0
    for role in ROLES:
        written += snapshot_role(
            out / "rollups-not-built", role, ids, lang="en", only={"usage", "usage_keys"}
        )
    call_command("rebuild_usage_rollups", stdout=io.StringIO())
    for role in ROLES:
        written += snapshot_role(out, role, ids, lang="en", only=None)
    for role in GERMAN_ROLES:
        written += snapshot_role(out, role, ids, lang="de", only=GERMAN_PAGES)
    print(f"snapshot: {written} pages in {out}")


if __name__ == "__main__":
    main()
