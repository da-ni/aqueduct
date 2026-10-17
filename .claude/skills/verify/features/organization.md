# Organization

Org admins and admins have an Organization page. It shows the org's number of people, teams, and service accounts, the default limits for keys in the org, and what is switched off for everyone in the org and for each team that switches off something of its own. Org admins edit the org's limits and its model and MCP server switch-offs there. Other users are sent to the Teams page.

## Sub-features

- `organization-overview` shows the counts, the limits card, and the switch-off table. The team count links to the Teams page, and each team row links to its team page.
- `organization-limits` edits the per-minute request and token limits and the hourly and daily multipliers. Invalid values come back as form errors.
- `organization-switch-offs` edits the switched-off models and MCP servers, and whether the server's default switch-offs also apply. Only models in the router config and servers in the MCP config can be chosen.
- `organization-scope` refuses the page to users and team admins, and refuses the edit pages with 404 to everyone but an org admin of that org or an admin.
- `organization-switch` shows the org an admin chose in the sidebar, as [org-switch.md](./org-switch.md) shows.

## How to get to it (user POV)

- Choose **Organization** under **Admin** in the sidebar. The path is `/aqueduct/management/organization/`.
- Choose **Edit** on the Limits card or the Switched off card. The paths are `/aqueduct/management/organization/<id>/limits/` and `/aqueduct/management/organization/<id>/switch-offs/`. `Verify Org` has ID 1.
- On the Teams page, an org admin's **Edit** next to the default limit opens the limits form.

## Driving it with aqv

Preconditions:

- A fresh launch. `Verify Org` has no limits and no switch-offs.

- **Overview.** Run `aqv login org-admin --next /aqueduct/management/organization/`. The page shows `6 people`, `1 team`, `1 service account`, `No limit` for each per-minute limit, and `Everyone in Verify Org` with `None` twice. Run `aqv shot organization/org-admin`.
- **Limit error.** Run `aqv click --role link --name "Edit limits"`, `aqv fill --label "Hourly limit, in per-minute limits" --value 61`, and `aqv click --role button --name "Save changes"`. The form shows `Must be between 1 and 60 (an hour has 60 minutes).`
- **Edit a limit.** Fill `Requests per minute, per key` with `600` and `Hourly limit, in per-minute limits` with `30`, then choose `Save changes`. The page shows `600` and `30`. Read `aqv shell -c "from management.models import Org; o = Org.objects.get(name='Verify Org'); print(o.requests_per_minute, o.hourly_limit_multiplier)"`.
- **Switch off a model.** Run `aqv click --role link --name "Edit switch-offs"`, `aqv click --role checkbox --name gpt-image-1`, and `aqv click --role button --name "Save changes"`. The row `Everyone in Verify Org` shows `gpt-image-1`. Then `aqv login user --next "/aqueduct/management/models/?model=gpt-image-1"` shows `OFF FOR YOU`, and a key created by `user` gets 404 `Model not found!` for `gpt-image-1` on `/v1/images/generations`.
- **Refused.** `aqv login user --next /aqueduct/management/organization/` lands on `/aqueduct/management/org/` with `You do not have permission to perform this action (Organization Admin required).` As `user`, `team-member`, `team-admin`, and `other-org-admin`, both edit paths of org 1 return 404.
- **Admin.** `aqv login admin --next /aqueduct/management/organization/` shows the `Organization` select in the sidebar. Choosing `Other Org` reloads this page for that org.

## Gotchas

- The edit pages answer 404, not a redirect, to a signed-in user who may not edit that org. Prove a refusal by the status and by reading the org with `aqv shell`.
- The number inputs have `min="0"`, so the browser blocks a negative value before it reaches the server. Use an out-of-range multiplier to see a server-side form error.
- Team switch-offs are still edited in the Django admin. The team rows only show them.
- `Personal keys per person` comes from the server setting `MAX_USER_TOKENS` and is not editable here.
- A stored switch-off that the config no longer lists shows as a checked box labelled `<name> (not in the current config, kept)`. Saving keeps it. Clearing the box removes it.
- Without an MCP config, the switch-off form has no MCP fields and keeps the stored MCP switch-offs.
