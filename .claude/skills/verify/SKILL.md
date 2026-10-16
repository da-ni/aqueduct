---
name: verify
description: Drive the Aqueduct management UI (server-rendered Django pages under /aqueduct/management/) in a private, seeded instance on quartz with headless Chromium, signed in as any role, and capture screenshots, page text, and database side effects as proof. Use it to prove a UI or permission change before a PR, to reproduce a UI bug, or to compare a page across roles.
---

# Verify the Aqueduct management UI

`scripts/aqv` starts a private Aqueduct instance from the checkout it lives in, with its own SQLite database, its own port, and its own headless Chromium. It seeds one account per role, a team, keys, and a week of requests. Every run is disposable. Never drive `aqueduct-dev.service` on port 8085. That instance holds the user's own dev data.

All commands below run from the repository root. Set `AQV=.claude/skills/verify/scripts/aqv`. Shell variables do not persist between tool calls, so repeat the assignment in each call, or write the full path.

## Launch

```bash
AQV=.claude/skills/verify/scripts/aqv
$AQV launch
```

Launch takes about 15 seconds. It migrates a fresh database, runs `scripts/seed.py`, starts the repository's mock OpenAI server (`aqueduct/mock_api/`) as the instance's upstream, starts `manage.py runserver --noreload` on a free port, and starts Chromium with a debug port. It is ready when it prints the run state as JSON and a final line `export AQV_RUN=/tmp/aqueduct-verify/run-<id>`. By then it has signed in as `user` over HTTP and loaded the API keys page. Pass that directory to later commands as `--run <dir>`, or set `AQV_RUN` in the same shell call. With exactly one live run, commands find it without either.

The instance runs the working tree as it was at launch, including uncommitted edits. Django does not reload in this mode. After you change Python code or templates, stop the run and launch a new one.

Seeded accounts, all with the email `<username>@verify.invalid`:

| Username | Role | Org | Notes |
|---|---|---|---|
| `admin` | admin group, `is_staff`, `is_superuser` | Verify Org | Can switch orgs and open the Django admin. |
| `org-admin` | org admin | Verify Org | |
| `team-admin` | user | Verify Org | Team admin of `Verify Team`. Owns the `verify-pipeline` service account. |
| `team-member` | user | Verify Org | Member of `Verify Team`. |
| `user` | user | Verify Org | In no team. |
| `other-org-admin` | org admin | Other Org | For cross-org checks. |
| `new-user` | user | Verify Org | Has not accepted the terms of service. |

The router config is `example_router_config.yaml` from the main checkout, set by the quartz dev overlay. Seeded requests use its model names.

## Call the gateway

The `/v1` API answers with mock upstream responses, so a key created in the UI works against the gateway. No real provider is involved. Copy the secret from the `Your key is ready` panel, then call the base URL from the run state.

```bash
BASE=$(python3 -c "import json; print(json.load(open('$AQV_RUN/state.json'))['base'])")
curl -s -H "Authorization: Bearer $KEY" "$BASE/v1/models"
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "hi"}]}' \
  "$BASE/v1/chat/completions"
```

Both return 200. Embeddings and files also work. Each call is stored as a request on the key. Responses come from `aqueduct/mock_api/mock_configs.py`, and the mock's log is `mock.log` in the run directory.

## Doctor

```bash
.claude/skills/verify/scripts/aqv doctor --run <dir>
```

It reads only. It checks that the server and browser processes are alive, the mock upstream answers, the login page answers 200, the browser debug port answers, the checkout HEAD still matches the SHA the run launched from, no migration is unapplied, and every seeded account exists. Exit code 0 means the run is worth driving. Run it first whenever a command fails in a way you did not expect.

## Drive

The browser keeps its page and cookies between commands. Every navigating command prints one JSON line with `status` (the HTTP status of the last page load), `url`, `title`, and `server_error_page` (true when Django's debug error page is showing).

- `aqv login <username> [--next <path>]` signs the browser in as a seeded account through `/verify-login/`, a route that exists only on verification instances. It clears cookies first.
- `aqv open <path>` loads a path, such as `/aqueduct/management/usage/?span=1w`.
- `aqv click`, `aqv fill --value <v>`, and `aqv select --value <v>` act on one element. Name it with `--role <role> --name <accessible name>`, `--label <label>`, `--text <text>`, or `--selector <css>` as a last resort. Add `--exact` for an exact match and `--nth <n>` to pick one of several matches. The browser accepts every JavaScript `confirm()` dialog.
- `aqv text [--selector main]` prints the visible text. `aqv aria [--selector main] [--save <name>]` prints or saves an ARIA snapshot.
- `aqv tour [--users ...] [--pages ...]` loads every page as every role, saves a screenshot of each, and writes `tour/summary.json`. It exits 2 when any page returns a 5xx status.
- `aqv shell -c "<python>"` runs Python in the run's Django shell. Use it to read side effects, such as `from management.models import Token; print(Token.objects.filter(name="x").exists())`.

For a signed-out check, use `curl` against the base URL from the run state, because `login` always signs someone in.

Feature recipes with the real button names live in [`features/README.md`](features/README.md). Read the index before driving a feature.

## Evidence

Evidence goes to `/tmp/aqueduct-verify/evidence/<run-id>/`, outside the run directory, so `stop` keeps it.

- `aqv shot <name> [--viewport]` saves a full-page screenshot as `<name>.png`. The name may contain subfolders.
- A proof follows the user's path through the UI, never a direct database write or a test-only endpoint other than `/verify-login/`.
- Capture the action and the resulting state. For a change that saves data, also read the stored value with `aqv shell`.
- For a permission check, prove both sides: the allowed role succeeds, and an out-of-scope role is refused and the database is unchanged.
- Report the run's SHA from `doctor`, and say when the tree was dirty at launch.

## Cleanup

```bash
.claude/skills/verify/scripts/aqv stop --run <dir>
```

It stops the server, mock upstream, and browser by the process group IDs recorded at launch, never by process name. It copies `server.log` and `mock.log` into the evidence folder, deletes the run directory with its database and browser profile, and prints where the evidence is. `aqv runs` lists every run and whether it is live. Stop every run you launched, including after a failed attempt.

## Helpers

- `scripts/aqv` is the only command you run. `aqv <command> --help` lists each command's options.
- `scripts/seed.py` seeds a fresh database. `launch` runs it, and nothing else should.
- `scripts/verify_settings.py` loads the quartz dev overlay from `~/Projects/quartz/bedrock/services/aqueduct-dev` with the run's own database, without the OIDC backend.
- `scripts/verify_urls.py` adds `/verify-login/` to the overlay's URLs.

Each run is isolated, so parallel runs in separate worktrees work. In a new worktree, the first `uv run` builds a virtual environment, so the first launch there is slower.
