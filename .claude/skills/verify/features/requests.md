# Requests

The request log lists single API requests, newest first, 50 per page, paged by cursor (`?before=` and `?after=`, never an offset). Each row shows the time, the key, the model and path, the status, the token counts, and the response time. A row opens to show the request ID with a copy button, the user agent, the user ID the client sent, the gateway overhead, the output speed, and what the status means. Users see their own personal keys and their teams' service accounts, never a request on someone else's key, even one that sends their email as user ID. Org admins see their whole org. Admins pick any org or all orgs. The scope is the same as on the usage page.

## Sub-features

- `requests-list` shows the rows, the count of matching requests (`<n> requests`), and the retention note `Requests are kept for <n> days`. A model name in a row filters the log on that model. With no rows, the page reads `No request matches these filters.` when a filter, a status tab, or a time frame other than `30 days` narrows it, and `No requests in the last 30 days.` otherwise.
- `requests-status` filters by `All`, `Failed` (4xx and 5xx), `2xx`, `429`, `Other 4xx`, or `5xx`, each tab with its count.
- `requests-filters` is the filter bar the Overview page shares: the `Key:` and `Model:` dropdowns and the time frame. The `Key:` dropdown holds a search field (`?key_q=`, up to 20 visible keys by name or owner email, shown with the dropdown open), the scope entries (`All keys in <org>` and `My keys` for org admins and admins, `All my keys` for users), and `Your keys`, the viewer's own keys. The log opens on `30 days`. There is no custom range, `?start=` and `?end=` are ignored, and an unknown `?span=` (such as the retired `all`) falls back to `30 days`.
- `requests-org` lets an admin choose one org or all orgs in the `Organization:` dropdown.
- `requests-detail` opens one row's details. `?request=<id>` without a cursor opens the page that starts at that request when the newest page does not hold it, as the links on a key's page do.
- `requests-pages` moves between pages with `Newer` and `Older`.

## How to get to it (user POV)

- Choose **Requests** in the sidebar. The path is `/aqueduct/management/requests/`.
- Choose a status tab. Pick a key or a model in its dropdown; each entry is a link and applies at once.
- Choose the arrow button at the start of a row to open its details.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed gives 280 requests in `Verify Org`.

- **User.** Run `aqv login user --next /aqueduct/management/requests/`. The page reads `52 requests`, and every row names `user-laptop`. `aqv click --role link --name Older` shows the last 2 rows, and `Newer` returns to the first page.
- **Team member.** Run `aqv login team-member --next /aqueduct/management/requests/`, then `aqv click --selector "summary:has-text('Key:')"` and `aqv text --selector "details[open] ul"`. The dropdown offers `All my keys`, then under `Your keys` `member-notebook` (`Personal`) and `verify-pipeline` (`Service account · Verify Team`).
- **Filters.** With the key dropdown open, run `aqv click --role link --name "verify-pipeline Service account · Verify Team"`. The path ends `?key=5`. Run `aqv click --selector "summary:has-text('Model:')"` and `aqv click --role link --name gpt-4.1-nano --exact --nth 0`. The path ends `?key=5&model=gpt-4.1-nano`. Run `aqv click --role link --name 429`. The page reads `1 request`.
- **Model cell.** Run `aqv open /aqueduct/management/requests/` and `aqv click --role link --name gpt-4.1-nano --exact --nth 1`. The path ends `?model=gpt-4.1-nano`.
- **Detail.** Run `aqv click --role link --name "Show details" --nth 0`. The row shows `Request ID` with a copy button, `User agent`, `User ID sent by the client`, `Gateway overhead`, `Output speed`, and `What the status means`. Run `aqv shot requests/detail-team-member`.
- **Org admin.** Run `aqv login org-admin --next /aqueduct/management/requests/`. The page reads `280 requests`, the same total as the usage page over one month.
- **Key search.** As `org-admin`, run `aqv open "/aqueduct/management/requests/?key_q=member"`. The key dropdown is open and lists `member-notebook` under `Matching keys`. As `other-org-admin`, `?key_q=verify` reads `No key matches “verify”`.
- **Other org.** Run `aqv login other-org-admin --next /aqueduct/management/requests/`. The page reads `No requests in the last 30 days.`
- **Admin org choice.** Run `aqv login admin --next /aqueduct/management/requests/`, then `aqv click --selector "summary:has-text('Organization:')"` and `aqv click --role link --name "All organizations"`. The path ends `?org=all`, and the count matches `aqv shell -c "from management.models import Request; print(Request.objects.count())"`.

## Gotchas

- The dropdown entries, the status tabs, the model names, and the row arrows are links. A model name is a link both in the `Model:` dropdown and in each row, so pick one with `--nth`: `0` is the dropdown entry.
- All requests of `user` are 2xx, so its `429`, `Other 4xx`, and `5xx` tabs read 0. Use `team-member` or `org-admin` for failed requests.
- The page counts only requests to models in the router config, as the usage page does.
- The browser keeps the theme between logins. Choose `Switch between light and dark` to change it before a screenshot.
