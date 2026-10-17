# Requests

The request log lists single API requests, newest first by default, 50 per page. Sorted by time it pages by cursor (`?before=` and `?after=`, each cursor `<micros>,<id>[,<rank>]`, plus `?page=last`). Sorted by another column it pages by offset (`?page=<n>` or `?page=last`). Each row shows the time (`17 Oct 16:45`, no year), the key, the model with the path under it, the status, the token counts, and the response time. The `Key` column is left out when the viewer has a single key (`user` in the seed) or `?key=` is set. Below the `md` breakpoint each row is a card: `<time> · <status> · <response>`, then `<model> · <input> → <output>`, then the key name when the `Key` column would show. A row opens to show only the fields with a value: `Request ID` with a copy button, `Error` (non-2xx only, the code, its phrase, and what it means), `User agent`, `User ID`, `Gateway overhead` (org admins and admins only), and `Output speed`. Users see their own personal keys and their teams' service accounts, never a request on someone else's key, even one that sends their email as user ID. Org admins see their whole org. Admins pick any org or all orgs. The scope is the same as on the usage page.

## Sub-features

- `requests-list` shows the rows, the position of the page (`1–50 of <n>`, or `<n> requests` where the position is unknown, as after a `?request=` deep link to the middle of the log), and, on the last page or with no rows only, the retention note `Requests are kept for <n> days`. A model name in a row filters the log on that model. With no rows, the page reads `No request matches these filters.` when a filter, a status tab, or a time frame other than `24 hours` narrows it, and `No requests in the last 24 hours.` otherwise.
- `requests-status` filters by `All`, `Failed` (4xx and 5xx), and one tab per status group of the Overview page, labelled as there, with its codes (`2xx`, `429`, `4xx`, `5xx`) as small secondary text. Each tab shows its count. The URL values are `?status=failed|2xx|429|4xx|5xx`.
- `requests-filters` is the filter bar the Overview page shares: the `Key:` and `Model:` dropdowns and the time frame. The `Key:` dropdown holds, for org admins and admins, a search field (`?key_q=`, up to 20 visible keys by name or owner email, shown with the dropdown open), `All keys`, and `My keys` with the viewer's own keys nested under it (hidden without own keys). Users get `All keys` with their keys nested under it. The dropdown hides when the viewer sees one key, and `Model:` hides with one model. The log opens on `24 hours`; `?span=1m` still opens 30 days. There is no custom range, `?start=` and `?end=` are ignored, and an unknown `?span=` (such as the retired `all`) falls back to `24 hours`.
- `requests-org` shows the org an admin chose in the sidebar, as [org-switch.md](./org-switch.md) shows.
- `requests-detail` opens one row's details. `?request=<id>` without a cursor opens the page that starts at that request when the newest page does not hold it. The links on a key's page add `span=1m`, so a request older than 24 hours still opens.
- `requests-pages` moves between pages with `First`, `Previous`, `Next`, and `Last`, shown only when there is more than one page. Each time cursor carries the rank of its row, so the position costs no count.
- `requests-search` is the `Search requests` field (`?q=`): an exact request ID, or text in the path, the user ID sent by the client, or the user agent, case-insensitive. It applies inside the filters, the tab, and the time frame, and the tab counts count the matches. `Clear` removes it.
- `requests-sort` sorts by the `Time`, `Model`, `Status`, `Tokens` (input plus output), and `Response` headings (`?sort=<column>` or `?sort=-<column>`; default `-time`). The sorted heading has `aria-sort` and an arrow. Org-wide selections over `30 days` (org admins and admins without a key or a model) sort by time only: the other headings are plain text, with no hint. The default `24 hours` sorts by every column.
- `requests-counts` caches the tab counts for 60 seconds per visibility scope, filters, period, and search, so paging and sorting fetch only rows.

## How to get to it (user POV)

- Choose **Requests** in the sidebar. The path is `/aqueduct/management/requests/`.
- Choose a status tab. Pick a key or a model in its dropdown; each entry is a link and applies at once.
- Choose the arrow button at the start of a row to open its details.
- Type in `Search requests` and choose `Search`. Choose a column heading to sort.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed gives 280 requests in `Verify Org`.

- **Default.** Run `aqv login user --next /aqueduct/management/requests/`. `24 hours` is the current time frame, the page has no `Key` column, and the retention note shows under the single page.
- **User.** Run `aqv open "/aqueduct/management/requests/?span=1m"`. The page reads `1–50 of 67` with no retention note. `aqv click --role link --name Next` reads `51–67 of 67`, `First` returns to `1–50 of 67`, and `Last` reads `18–67 of 67` with the retention note. `Previous` from there returns to the first page, because fewer than 50 requests come before it.
- **Team member.** Run `aqv login team-member --next /aqueduct/management/requests/`, then `aqv click --selector "summary:has-text('Key:')"` and `aqv text --selector "details[open] ul"`. The dropdown offers `All keys`, with `member-notebook` and `verify-pipeline` (`Service account · Verify Team`) nested under it.
- **Filters.** With the key dropdown open, run `aqv click --role link --name "verify-pipeline Service account · Verify Team"`. The path ends `?key=5`. Run `aqv click --selector "summary:has-text('Model:')"` and `aqv click --role link --name gpt-4.1-nano --exact --nth 0`. The path ends `?key=5&model=gpt-4.1-nano`. Run `aqv click --role link --name Rate-limited`. The `Key` column is gone.
- **Model cell.** Run `aqv open /aqueduct/management/requests/` and `aqv click --role link --name gpt-4.1-nano --exact --nth 1`. The path ends `?model=gpt-4.1-nano`.
- **Detail.** Run `aqv open "/aqueduct/management/requests/?status=failed"` and `aqv click --role link --name "Show details" --nth 0`. The row shows `Request ID` with a copy button, `Error` (`429 Too Many Requests. …`), `User agent`, and `User ID`, and no `Gateway overhead`. Run `aqv shot requests/detail-team-member`.
- **Org admin.** Run `aqv login org-admin --next "/aqueduct/management/requests/?span=1m"`. The page reads `1–50 of 280`, the same total as the usage page over one month. The `Model`, `Status`, `Tokens`, and `Response` headings are plain text. A row's details include `Gateway overhead`. Run `aqv open "/aqueduct/management/requests/?span=1w&sort=-tokens"`; the page reads `1–50 of <n>`, a few under 280 depending on the launch time, sorted by tokens. Run `aqv fill --label "Search requests" --value curl` and `aqv click --role button --name Search`; the page reads `1–50 of 115`.
- **Key search.** As `org-admin`, run `aqv open "/aqueduct/management/requests/?key_q=member"`. The key dropdown is open and lists `member-notebook` under `Matching keys`. As `other-org-admin`, `?key_q=verify` reads `No key matches “verify”`.
- **Other org.** Run `aqv login other-org-admin --next /aqueduct/management/requests/`. The page reads `No requests in the last 24 hours.` and the retention note.
- **Admin org choice.** Run `aqv login admin --next /aqueduct/management/requests/`, then `aqv select --label Organization --value all`. The page reloads, and the count matches `aqv shell -c "from management.models import Request; print(Request.objects.count())"`.

## Gotchas

- The dropdown entries, the status tabs, the model names, and the row arrows are links. A model name is a link both in the `Model:` dropdown and in each row, so pick one with `--nth`: `0` is the dropdown entry.
- The seeded requests spread over a week, so counts in these recipes use `?span=1m`; the 24-hour default holds whatever the seed placed in the last day.
- Below 768 px the table is a list of cards. `aqv` fixes the viewport at 1440 px; drive a 390 px check through Playwright over the run's `cdp_port` with a new context of that width.
- All requests of `user` are 2xx, so its `Rate-limited`, client error, and server error tabs read 0. Use `team-member` or `org-admin` for failed requests.
- The page counts only requests to models in the router config, as the usage page does.
- The browser keeps the theme between logins. Choose `Switch between light and dark` to change it before a screenshot.
