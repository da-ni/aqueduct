# Organization

Org admins and admins have an Organization page. It shows the org's number of teams and service accounts, the default limits for keys in the org, and what is unavailable to everyone in the org and to each team that has its own list. Org admins edit the org's limits and its available models and MCP servers there. Other users are sent to the Teams page.

## Sub-features

- `organization-overview` shows the counts, the limits card, and the `Unavailable` table. The limits card has the note `Default for every key. Team and personal limits override it.`, then `Hourly cap` and `Daily cap` as minutes of full rate (`60 min of full rate (default)` and `1,440 min of full rate (default)` when unset), and `Personal keys per person` `3 (server setting)`. The team count links to the Teams page, and each team row links to its team page.
- `organization-limits` edits the per-minute request and token limits and the hourly and daily multipliers. Invalid values come back as form errors.
- `organization-availability` edits the available models and MCP servers as checklists, where a checked box means available and every box starts checked. `Select all` and `Select none` appear above a list of more than six. Whether the server's default unavailable names apply shows only when the server setting names some. Only models in the router config and servers in the MCP config can be chosen.
- `organization-scope` refuses the page to users and team admins, and refuses the edit pages with 404 to everyone but an org admin of that org or an admin.
- `organization-switch` shows the org an admin chose in the sidebar, as [org-switch.md](./org-switch.md) shows. On `All organizations` the page is an `Organizations` table of every org with its team count. Each name links to `?org=<id>`, which switches the viewed org and opens its page.

## How to get to it (user POV)

- Choose **Organization** under **Admin** in the sidebar. The path is `/aqueduct/management/organization/`.
- Choose **Edit** on the Limits card or the Unavailable card. The paths are `/aqueduct/management/organization/<id>/limits/` and `/aqueduct/management/organization/<id>/availability/`. `Verify Org` has ID 1.
- On the Teams page, an org admin's **Edit** next to the default limit opens the limits form.

## Driving it with aqv

Preconditions:

- A fresh launch. `Verify Org` has no limits and nothing unavailable.

- **Overview.** Run `aqv login org-admin --next /aqueduct/management/organization/`. The page shows `1 team`, `1 service account`, `No limit` for each per-minute limit, `60 min of full rate (default)`, and `Everyone in Verify Org` with `None` twice. Run `aqv shot organization/org-admin`.
- **Limit error.** Run `aqv click --role link --name "Edit limits"`, `aqv fill --label "Hourly cap" --value 61`, and `aqv click --role button --name "Save changes"`. The form shows `Must be between 1 and 60 (an hour has 60 minutes).`
- **Edit a limit.** Fill `Requests per minute` with `600` and `Hourly cap` with `30`, then choose `Save changes`. The page shows `600` and `30 min of full rate`. Read `aqv shell -c "from management.models import Org; o = Org.objects.get(name='Verify Org'); print(o.requests_per_minute, o.hourly_limit_multiplier)"`.
- **Make a model unavailable.** Run `aqv click --role link --name "Edit availability"`, `aqv click --role checkbox --name gpt-image-1`, and `aqv click --role button --name "Save changes"`. The row `Everyone in Verify Org` shows `gpt-image-1`, and `Org.excluded_models` is `['gpt-image-1']`. Then `aqv login user --next /aqueduct/management/models/` lists no `gpt-image-1`, and a key created by `user` gets 404 `Model not found!` for `gpt-image-1` on `/v1/images/generations`.
- **Refused.** `aqv login user --next /aqueduct/management/organization/` lands on `/aqueduct/management/org/` with `You do not have permission to perform this action (Organization Admin required).` As `user`, `team-member`, `team-admin`, and `other-org-admin`, both edit paths of org 1 return 404.
- **Admin.** `aqv login admin --next /aqueduct/management/organization/` shows the `Viewing` select in the sidebar. Choosing `Other Org` reloads this page for that org.
- **All organizations.** As `admin`, run `aqv open "/aqueduct/management/organization/?org=all"`. The page shows the heading `Organizations` and rows `Other Org` and `Verify Org` with their team counts. Run `aqv click --role link --name "Other Org"`. The page shows `Other Org`, and the sidebar select shows `Other Org`. Run `aqv shot organization/all-orgs-admin` before the click.

## Gotchas

- The edit pages answer 404, not a redirect, to a signed-in user who may not edit that org. Prove a refusal by the status and by reading the org with `aqv shell`.
- The number inputs have `min="0"`, so the browser blocks a negative value before it reaches the server. Use an out-of-range multiplier to see a server-side form error.
- User availability is edited in the Django admin, under **Availability** with the same checked-means-available lists. Team availability is edited on the team page at `/aqueduct/management/team/<id>/availability/` by org admins of the team's org and admins; everyone else gets 404. The team page shows a team's unavailable lists and the `Edit availability` link to those roles only. A save posts `<field>-shown` with each list, and a list posted without it is left as stored.
- `Personal keys per person` comes from the server setting `MAX_USER_TOKENS` and is not editable here.
- A stored unavailable name that the config no longer lists shows as a cleared box labelled `<name> (not in the current config)`. Saving keeps it. Checking the box removes it.
- Without an MCP config, the availability form has no MCP fields and keeps the stored MCP list.
