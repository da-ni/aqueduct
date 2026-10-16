# Overview

The Overview page shows usage of the keys the viewer may see. Users see their own keys and their teams' service accounts. Org admins see every key in their org. Admins pick any org or all orgs.

## Sub-features

- `usage-totals` shows six cards: input tokens, output tokens, cache hits, requests, failed share, and median response time. Each card except the median has a sparkline. When the previous period of the same length is still within request retention, each card shows its change against it.
- `usage-chart` shows tokens or requests per minute, hour, or day, with a `Tokens` and `Requests` switch.
- `usage-statuses` splits requests into answered, rate-limited, other client errors, and provider failures.
- `usage-heat` shows average tokens per hour for each weekday and hour over the last 28 days, whatever the time frame.
- `usage-models` lists every model with requests in the time frame, with tokens, cache hits, requests, median response, and share.
- `usage-keys` lists the keys that used the most tokens. A key links to its detail page only for its owner, and a service account links to its team page only for viewers of that team. With `?token=<id>`, the list shows tokens per `user` value instead.
- `usage-orgs` lists tokens per org when an admin views all orgs.
- `usage-filters` covers the time frame, a custom UTC range, one model, `My keys` for org admins and admins, and the org dropdown for admins.

## How to get to it (user POV)

- Choose **Overview** in the sidebar. The path is `/aqueduct/management/usage/`.
- Choose a time frame: `1 hour`, `24 hours`, `7 days` (the default), or `30 days`. `Custom range` opens a form with UTC start and end times.
- Choose a model in the `All models` dropdown.
- As an admin, choose an org in the dropdown that names the current org, or `All organizations`.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed spreads 280 requests over the last 7 days across all seeded keys.

- **Org admin view.** Run `aqv login org-admin --next /aqueduct/management/usage/`. The `Requests` card shows more than 250, and `Largest keys` lists all five seeded keys. Only `org-admin-scripts` and `verify-pipeline` are links. Run `aqv shot usage/week-org-admin`.
- **User view.** Run `aqv login user --next /aqueduct/management/usage/`. `Largest keys` lists only `user-laptop`, and the page has no `Everyone` and `My keys` switch.
- **Team admin view.** Run `aqv login team-admin --next /aqueduct/management/usage/`. `Largest keys` lists `verify-pipeline` and `team-admin-cli`.
- **Admin, all orgs.** Run `aqv login admin`, then `aqv open "/aqueduct/management/usage/?org=all&span=1m"`. `By organization` appears, and the `Requests` card shows 280. No card shows a change, because the previous 30 days are outside retention.
- **Key filter scope.** As `user`, run `aqv open "/aqueduct/management/usage/?token=<id of team-member's key>"`. The status is 404.
- **Cross-check.** Run `aqv shell -c "from management.models import Request; print(Request.objects.count())"` and compare it with the admin's all-orgs 30-day total.

## Gotchas

- `aqv login --next` drops everything after the first `&` in the path. Sign in first, then `aqv open` a path with more than one parameter.
- The time frames end with the current minute, hour, or day and start at a whole bucket. `7 days` is today and the six days before it, not the last 168 hours, so the oldest seeded requests fall into the previous period. That makes the seeded change figures very large.
- The seed puts no requests in `Other Org`, so `By organization` lists only `Verify Org`.
- The time frame buttons, the `Everyone` and `My keys` switch, and the dropdown entries are links. Click them with `--role link`.
