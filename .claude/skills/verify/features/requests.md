# Requests

The request log lists single API requests, newest first, 50 per page. Each row shows the time, the key, the model and path, the status, the token counts, and the response time. A row opens to show the user agent, the user ID the client sent, the gateway overhead, the output speed, and what the status means. Users see their own keys, their teams' service accounts, and requests sent with their email as user ID. Org admins see their whole org. Admins pick any org or all orgs. The scope is the same as on the usage page.

## Sub-features

- `requests-list` shows the rows, the count, and the retention note `Requests are kept for <n> days`.
- `requests-status` filters by `All`, `2xx`, `429`, `Other 4xx`, or `5xx`, each tab with its count.
- `requests-key` and `requests-model` filter by one key or one model.
- `requests-org` lets an admin choose one org or all orgs.
- `requests-detail` opens one row's details.
- `requests-pages` moves between pages with `Newer` and `Older`.

## How to get to it (user POV)

- Choose **Requests** in the sidebar. The path is `/aqueduct/management/requests/`.
- Choose a status tab. Pick a key or a model in the selects, then choose `Apply`.
- Choose the arrow button at the start of a row to open its details.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed gives 280 requests in `Verify Org`.

- **User.** Run `aqv login user --next /aqueduct/management/requests/`. The page reads `Showing 1–50 of 52`, and every row names `user-laptop`.
- **Team member.** Run `aqv login team-member --next /aqueduct/management/requests/`. The `Key` select offers `member-notebook · Personal` and `verify-pipeline · Service account · Verify Team`.
- **Filters.** As `team-member`, run `aqv select --label Key --value 5`, `aqv select --label Model --value gpt-4.1-nano`, and `aqv click --role button --name Apply`. The path ends `?key=5&model=gpt-4.1-nano`. Run `aqv click --role link --name 429`. One row remains.
- **Detail.** Run `aqv click --role link --name "Show details" --nth 0`. The row shows `User agent`, `User ID sent by the client`, `Gateway overhead`, `Output speed`, and `What the status means`. Run `aqv shot requests/detail-team-member`.
- **Org admin.** Run `aqv login org-admin --next /aqueduct/management/requests/`. The page reads `of 280`, the same total as the usage page over one month.
- **Other org.** Run `aqv login other-org-admin --next /aqueduct/management/requests/`. The page reads `No request matches.`
- **Admin org choice.** Run `aqv login admin --next /aqueduct/management/requests/`, then `aqv select --label Organization --value all` and `aqv click --role button --name Apply`. The count matches `aqv shell -c "from management.models import Request; print(Request.objects.count())"`.

## Gotchas

- The selects apply only after `Apply`. The status tabs and the row arrows are links.
- All requests of `user` are 2xx, so its `429`, `Other 4xx`, and `5xx` tabs read 0. Use `team-member` or `org-admin` for failed requests.
- The page counts only requests to models in the router config, as the usage page does.
- The browser keeps the theme between logins. Choose `Switch between light and dark` to change it before a screenshot.
