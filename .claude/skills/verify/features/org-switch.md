# Organization switch

Admins choose the org they look at, one org or all orgs, in the `Viewing` select at the top of the sidebar. The session keeps the choice, and the Overview, All keys, Requests, Teams, and Organization pages show that org. Teams shows one org at a time, so on `All organizations` it shows the admin's own org with the note `This page shows one organization at a time, so it shows your own.` Organization instead lists every org, and a name opens that org through `?org=<id>`. A `?org=<id>` or `?org=all` link applies that org and keeps it in the session. Org admins and users get no select, and `?org=` changes nothing for them.

## Sub-features

- `org-switch-shell` is the select at the top of the sidebar, for admins only. It lists `All organizations` and every org by name, and changing it reloads the current page.
- `org-switch-session` keeps the choice for every later page of the session.
- `org-switch-link` applies `?org=` on any page and keeps it.
- `org-switch-reset` drops the key, the key search, and the page or cursor of the old org on a switch, and keeps the model, time frame, scope, and status tab.
- `org-switch-scope` ignores the select's form and `?org=` for org admins and users.

## How to get to it (user POV)

- Sign in as `admin`. The select is at the top of the sidebar on every management page. At 390 px wide, open the sidebar with `Open navigation` first.

## Driving it with aqv

Preconditions:

- A fresh launch. `admin` is in `Verify Org`. The seed's requests are all in `Verify Org`, so `Other Org` shows none.

- **Switch once.** Run `aqv login admin --next /aqueduct/management/usage/` and `aqv select --label Viewing --value 2`. The page reloads with `Viewing Other Org.` Then `aqv open` each of `/aqueduct/management/usage/keys/`, `/aqueduct/management/requests/`, `/aqueduct/management/org/`, and `/aqueduct/management/organization/`. Each shows `Other Org` and no `Verify Org` data. Run `aqv shot org-switch/<page>-admin` on each.
- **All organizations.** Choose `All organizations` in the select. The Overview shows `By organization`. The Teams page shows `Verify Org` with the fallback note. The Organization page shows the `Organizations` list.
- **Link.** Run `aqv open "/aqueduct/management/usage/?org=2"`, then `aqv open /aqueduct/management/requests/`. The select shows `Other Org`.
- **Reset.** On `/aqueduct/management/requests/?key=1&span=1d`, switch the org. The address keeps `span=1d` and has no `key`.
- **Refused.** As `org-admin` and `user`, no page has the select, and `aqv open "/aqueduct/management/usage/?org=2"` still shows `Verify Org`.

## Gotchas

- `Verify Org` has ID 1 and `Other Org` ID 2.
- The admin's own org in the database does not change on a switch. Read it with `aqv shell -c "from management.models import UserProfile; print(UserProfile.objects.get(user__username='admin').org)"`.
- `aqv` resets the viewport to 1440 px on every command. Check 390 px with Playwright over the run's `cdp_port`.
