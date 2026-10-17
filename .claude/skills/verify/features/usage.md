# Overview

The Overview page shows usage of the keys the viewer may see. Users see their own keys and their teams' service accounts. Org admins see every key in their org. Admins pick any org or all orgs. Signing in opens it for every role, unless the sign-in carries a `next` path on this site.

## Sub-features

- `usage-totals` shows five cards: `Input from cache`, `New input`, `Output`, `Requests`, and `Failed` (a percentage). Each card has a sparkline and no second line. The cards are not links. Nothing compares with the previous period.
- `usage-chart` shows tokens or requests per minute, hour, or day, with a `Tokens` and `Requests` switch. The page holds both series, so with scripts the switch sends no request: it swaps the series, sets `?metric=requests` in the address, and writes it into the links and searches that reload the page. Without scripts each switch link loads the page.
- `usage-statuses` splits requests into answered, rate-limited, other client errors, and `Model or provider error` (5xx). Each row opens the request log on its group. `Open the log` opens it with the same filters.
- `usage-heat` is the last section, `Activity across <org>` (`all organizations` for an admin on all orgs), set apart by a rule. It shows average tokens per hour for each weekday and hour over the last 28 days for every key of the org, whatever the time frame, key, model, or scope, so a user sees org-wide figures here. It reads the hourly rollups plus the requests they do not hold yet, so a new request shows at once. While the rollups lag more than 2 hours behind, it reads the requests and caches the result per org for an hour.
- `usage-models` lists every model with requests in the time frame, with tokens, cache hits, requests, median response, and share. A whole row opens the request log for that model. For an org-wide view (org admin or admin, no `?scope=mine`, no `?key=`, any span but `1h`) the figures come from the usage rollups, and each median reads `≈ <value>` with an `Approximate: within about 5%, or 1 ms for very fast responses` tooltip on the `Median` header (screen readers hear "approximately" and the tooltip text). Every other view shows exact medians without `≈`.
- `usage-keys` (`Top keys`) lists the 10 keys that used the most tokens in a table laid out like `usage-models`: the same split token bar and the same columns, requests included, but no `Median`. A whole row opens the key's detail page when the viewer may open it: their own personal keys and the service accounts of their teams. With `?key=<id>`, the page shows one key, and `By user ID` replaces the list.
- `usage-all-keys` is its own page at `/aqueduct/management/usage/keys/`, reached by `View all keys` under `Top keys`, which shows only when more visible keys have requests in the period than `Top keys` lists. Its `Overview` breadcrumb returns to the Overview with the same filters. It lists every visible key with requests in the period, 50 per page, in the `Top keys` table. It takes the same filters (`?org=`, `?scope=mine`, `?model=`, `?span=`), plus `?q=` (key name, or owner email for org admins and admins), `?sort=tokens|requests|name`, and `?page=`. `?key=` is ignored. The `Key:` dropdown shows only `All keys in <org>` and `My keys`, and users get no `Key:` dropdown. Share is of every visible key's tokens, also while searching, and bars scale to the busiest key.
- `usage-orgs` lists tokens per org when an admin views all orgs.
- `usage-filters` covers the time frame, one model, the org dropdown for admins, and the `Key:` dropdown: a key search (`?key_q=`), `All keys in <org>` and `My keys` for org admins and admins, `All my keys` for users, and the viewer's own keys.

## How to get to it (user POV)

- Choose **Overview** in the sidebar. The path is `/aqueduct/management/usage/`.
- Choose a time frame: `1 hour`, `24 hours`, `7 days` (the default), or `30 days`. There is no custom range, `?start=` and `?end=` are ignored, and an unknown `?span=` (such as the retired `all`) falls back to `7 days`.
- Choose a key in the `Key: All keys` dropdown, or a model in the `Model: All models` dropdown.
- As an admin, choose an org, or `All organizations`, in the sidebar's `Organization` select. See [org-switch.md](./org-switch.md).

## Driving it with aqv

Preconditions:

- A fresh launch. The seed spreads 280 requests over the last 7 days across all seeded keys.

- **Landing.** Run `aqv login org-admin`, `aqv login user`, and `aqv login admin` without `--next`. Each JSON line shows `url` ending in `/aqueduct/management/usage/`. `aqv open /` lands there too.
- **Org admin view.** Run `aqv login org-admin --next /aqueduct/management/usage/`. The `Requests` card shows more than 250, and `Top keys` lists all five seeded keys. Only `org-admin-scripts` and `verify-pipeline` are links. Under `Requests`, the 5xx row reads `Model or provider error`. Run `aqv shot usage/week-org-admin`.
- **User view.** Run `aqv login user --next /aqueduct/management/usage/`. `Top keys` lists only `user-laptop`, `Activity across Verify Org` still shows the whole org, and the `Key:` dropdown has no `My keys` entry.
- **Team admin view.** Run `aqv login team-admin --next /aqueduct/management/usage/`. `Top keys` lists `verify-pipeline` and `team-admin-cli`.
- **Admin, all orgs.** Run `aqv login admin`, then `aqv open "/aqueduct/management/usage/?org=all&span=1m"`. `By organization` appears, the `Requests` card shows 280, and the last section reads `Activity across all organizations`.
- **Drill-downs.** As `org-admin`, run `aqv click --role link --name "gpt-4.1-nano, open its requests in the request log"`. The path is `/aqueduct/management/requests/?span=1w&model=gpt-4.1-nano`. Back on the Overview, `aqv click --role link --name verify-pipeline --exact` opens `/aqueduct/management/tokens/5/`.
- **All keys.** As `org-admin`, the Overview has no `View all keys`, because `Top keys` already lists all five seeded keys. Add six keys with requests through `aqv shell`, reload, and run `aqv click --role link --name "View all keys"`. The path is `/aqueduct/management/usage/keys/?span=1w`, and its first ten rows are the `Top keys` in the same order. `Overview` in the breadcrumb returns to `/aqueduct/management/usage/?span=1w`. `aqv fill --label "Search keys" --value "team-admin@"` and `aqv click --role button --name Search` list `team-admin-cli` and `verify-pipeline`. As `team-member`, `aqv open "/aqueduct/management/usage/keys/?q=team-admin%40"` shows `0 keys`, because users cannot search by owner. The seed has five keys, so to see `Next`, add more than 50 keys with requests through `aqv shell` first.
- **Key filter scope.** As `user`, run `aqv open "/aqueduct/management/usage/?key=2"` (team-member's `member-notebook`). The status is 404. `?key=1` (`user-laptop`) shows `for the key user-laptop` and `By user ID`.
- **Cross-check.** Run `aqv shell -c "from management.models import Request; print(Request.objects.count())"` and compare it with the admin's all-orgs 30-day total.

## Gotchas

- The time frames end with the current minute, hour, or day and start at a whole bucket. `7 days` is today and the six days before it, not the last 168 hours.
- The seed builds no usage rollups, so org-wide figures read the requests until you run `aqv shell -c "from django.core.management import call_command; call_command('rebuild_usage_rollups')"`. The figures are the same either way, and the `≈` medians are approximate either way. Run it to exercise the rollup path, for example after a change to `usage_rollup.py`.
- The seed puts no requests in `Other Org`, so `By organization` lists only `Verify Org`.
- The time frame buttons and the dropdown entries are links. The key search is a form inside the `Key:` dropdown; `aqv open` with `?key_q=` is the short way to it. Click them with `--role link`. Open a dropdown first with `aqv click --selector "summary:has-text('Key:')"` (or `Model:`).
