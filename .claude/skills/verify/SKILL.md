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

Launch takes about 15 seconds. It migrates a fresh database, runs `scripts/seed.py`, starts the repository's mock OpenAI server (`aqueduct/mock_api/`) as the instance's upstream, starts `manage.py runserver --noreload` on a free port, and starts Chromium with a debug port. It is ready when it prints the run state as JSON and a final line `export AQV_RUN=/tmp/aqueduct-verify/run-<id>`. By then it has signed in as `user` over HTTP and landed on the Overview. Pass that directory to later commands as `--run <dir>`, or set `AQV_RUN` in the same shell call. With exactly one live run, commands find it without either.

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

The router config is `example_router_config.yaml` and the MCP config is `test-mcp.json`, both from the checkout `aqv` runs from. Set `LITELLM_ROUTER_CONFIG_FILE_PATH` or `MCP_CONFIG_FILE_PATH` for `aqv launch` and every `aqv shell` call on that run to use another file. Seeded requests use the router config's model names.

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

- `aqv login <username> [--next <path>]` signs the browser in as a seeded account through `/verify-login/`, a route that exists only on verification instances. It clears cookies first. Without `--next` it lands where a real sign-in lands, the Overview. Like a real sign-in, it sets the language cookie from the account's saved language, so an account whose language was chosen on the Settings page sees the pages in that language. An account with no saved language gets English, the language of the headless browser.
- `aqv open <path>` loads a path, such as `/aqueduct/management/usage/?span=1w`.
- `aqv click`, `aqv fill --value <v>`, and `aqv select --value <v>` act on one element. Name it with `--role <role> --name <accessible name>`, `--label <label>`, `--text <text>`, or `--selector <css>` as a last resort. Add `--exact` for an exact match and `--nth <n>` to pick one of several matches. The browser accepts every JavaScript `confirm()` dialog.
- `aqv text [--selector main]` prints the visible text. `aqv aria [--selector main] [--save <name>]` prints or saves an ARIA snapshot.
- `aqv tour [--users ...] [--pages ...] [--langs de en] [--widths 1440 1024 390] [--layout]` loads every page as every role, saves a screenshot of each, and writes `tour/summary.json`. It exits 2 when any page returns a 5xx status. `--langs` sets the language cookie after each sign-in, and `--widths` repeats every page at each window width; screenshots then go to `tour/<lang>/<width>/<user>/`. A user written as `admin@all` signs in as `admin` and switches to all organizations. `--layout` runs `scripts/layout_check.js` on every page and lists what it finds under each page and in the summary: `overflows` and `clipped` for text that runs past its box, `truncated` for an ellipsis with no `title` that shows the full text, `scrolls` for a scroll container on a window 768 px or wider, `page` for a page wider than the window, and `wraps` for a label of 32 characters or fewer that breaks over two lines. Wrapping is a finding to look at, not always a fault: a German table heading is meant to wrap between words.
- `aqv shell -c "<python>"` runs Python in the run's Django shell. Use it to read side effects, such as `from management.models import Token; print(Token.objects.filter(name="x").exists())`.

For a signed-out check, use `curl` against the base URL from the run state, because `login` always signs someone in.

Feature recipes with the real button names live in [`features/README.md`](features/README.md). Read the index before driving a feature.

## Evidence

Evidence goes to `/tmp/aqueduct-verify/evidence/<run-id>/`, outside the run directory, so `stop` keeps it.

- `aqv shot <name> [--viewport] [--width <px>]` saves a full-page screenshot as `<name>.png`. The name may contain subfolders. `--width 390` takes it at a phone width; every other command uses 1440. For dark mode, run `aqv click --selector "[data-toggle-theme]"` once: the browser keeps the theme for later commands and sign-ins.
- A proof follows the user's path through the UI, never a direct database write or a test-only endpoint other than `/verify-login/`.
- Capture the action and the resulting state. For a change that saves data, also read the stored value with `aqv shell`.
- For a permission check, prove both sides: the allowed role succeeds, and an out-of-scope role is refused and the database is unchanged.
- Report the run's SHA from `doctor`, and say when the tree was dirty at launch.

## Prove a change is invisible

A refactor that should change nothing a user sees is proven with two page snapshots. `aqv snapshot` needs no running instance.

```bash
AQV=.claude/skills/verify/scripts/aqv
git worktree add /tmp/aqueduct-before <base-sha>
$AQV snapshot /tmp/snap-before --checkout /tmp/aqueduct-before
$AQV snapshot /tmp/snap-after
$AQV snapshot-diff /tmp/snap-before /tmp/snap-after
```

`snapshot` takes under a minute. It migrates a fresh SQLite database, runs `scripts/seed.py` with 30 days of requests, adds a key in the other org, unfinished requests, and a request for a model the router no longer serves, and freezes the clock at 2026-10-15 10:30 UTC. It then renders every management page with the Django test client as `user`, `team-member`, `team-admin`, `org-admin`, `admin`, and `admin@all`, with the filter, sort, search, and pager variants of Overview, All keys, and Requests. Overview and All keys are rendered once before the usage rollups are built, under `rollups-not-built/`, and every page once after. `user` and `org-admin` repeat a subset in German under `de/`. Each page becomes one file with its status, redirect target, the visible text of `<main>` one text node per line, and the links in `<main>`.

The code that renders comes from `--checkout`, and the seed and page list from the checkout `aqv` lives in, so both snapshots use the same data. Two snapshots of the same code are identical. `snapshot-diff` prints a unified diff and exits 0 only when nothing differs.

## Cleanup

```bash
.claude/skills/verify/scripts/aqv stop --run <dir>
```

It stops the server, mock upstream, and browser by the process group IDs recorded at launch, never by process name. It copies `server.log` and `mock.log` into the evidence folder, deletes the run directory with its database and browser profile, and prints where the evidence is. `aqv runs` lists every run and whether it is live. Stop every run you launched, including after a failed attempt.

## Helpers

- `scripts/aqv` is the only command you run. `aqv <command> --help` lists each command's options.
- `scripts/seed.py` seeds a fresh database. `launch` and `snapshot` run it, and nothing else should.
- `scripts/snapshot.py` renders the pages for `aqv snapshot`.
- `scripts/verify_settings.py` loads the quartz dev overlay from `~/Projects/quartz/bedrock/services/aqueduct-dev` with the run's own database, without the OIDC backend.
- `scripts/verify_urls.py` adds `/verify-login/` to the overlay's URLs.

Each run is isolated, so parallel runs in separate worktrees work. In a new worktree, the first `uv run` builds a virtual environment, so the first launch there is slower.
