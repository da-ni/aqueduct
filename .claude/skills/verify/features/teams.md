# Teams

Users see the teams they may open on the Teams page, with members, service accounts, the request limit, and seven days of service account tokens. A team's page shows seven-day totals, its service accounts, its members, and its limits. Team admins manage members and service accounts. Org admins also create and delete teams, appoint team admins, and set team limits in the Edit team form.

## Sub-features

- `teams-list` lists the teams a user may see: their own with a `Team admin` badge where they manage it, or every team in the org for an org admin. A user in no team sees one card, `You're not in a team. Ask your org admin to add you.`, with no table or search. A search box filters by team name. The list shows 50 teams per page. Admins see the org they chose in the sidebar, and with `All organizations` every team with an `Organization` column, as [org-switch.md](./org-switch.md) shows.
- `team-detail` shows three cards under `Service accounts, last 7 days` (`Tokens`, `Requests`, `Failed`), service accounts with owner, tokens, last use, and expiry, its members with their role (team admins first, 50 per page, `?q=` searches emails), and its limits and unavailable models. The limits card is hidden from members and team admins when every limit reads `No limit`.
- `team-limits` lets an org admin or admin set the team's three per-minute limits in the Edit team form.
- `team-members` adds and removes members.
- `team-admins` makes a member a team admin or removes the role.
- `team-service-accounts` creates, rotates, edits, transfers, and deletes service accounts.
- `team-create-delete` creates teams, and deletes them from the bottom of the Edit team form.
- `team-scope` refuses team pages and actions outside the user's teams or org.

## How to get to it (user POV)

- Choose **Teams** in the sidebar. The path is `/aqueduct/management/org/`.
- Choose a team name in the table. The path is `/aqueduct/management/team/<id>`. The seeded `Verify Team` has ID 1.
- Choose **Create team** on the Teams page, as an org admin.

## Driving it with aqv

Preconditions:

- A fresh launch. `Verify Team` has the team admin `team-admin`, the member `team-member`, and the service account `verify-pipeline` owned by `team-admin`.

- **List as a member.** Run `aqv login team-member --next /aqueduct/management/org/`. The table shows `Verify Team` with an empty `Your role` cell, `2` members, and `1` service account. As `team-admin`, the cell shows the `Team admin` badge.
- **List as a user in no team.** Run `aqv login user --next /aqueduct/management/org/`. The page shows only `You're not in a team. Ask your org admin to add you.`
- **List as org admin.** Run `aqv login org-admin --next /aqueduct/management/org/`. The page shows a `Create team` link and no `Your role` column.
- **List every org.** Run `aqv login admin --next "/aqueduct/management/org/?org=all"`. The page reads `All organizations` and lists `Verify Team` and `Other Team` with their `Organization`.
- **Create a team.** As `org-admin`, run `aqv click --role link --name "Create team"`, `aqv fill --label Name --value "Proof Team"`, then `aqv click --role button --name "Create team"`. The page returns to the Teams page with `Proof Team` in the table.
- **Detail as team admin.** Run `aqv login team-admin --next /aqueduct/management/team/1`. The page shows `Edit`, `Add service account`, `1 of 10 service accounts`, the `Service accounts` table with `verify-pipeline`, and `Members`. The row of `team-admin@verify.invalid` has no remove button. It shows `Owns verify-pipeline · Transfer`, because that user owns a service account here. As `team-member`, the header shows `1 service account`.
- **Add a member.** On the same page, run `aqv click --text "Add member" --exact`, `aqv fill --label "Email of the new member" --value user@verify.invalid`, and `aqv click --role button --name Add --exact`. `user@verify.invalid` appears under `Members`. An email outside `Verify Org`, such as `other-org-admin@verify.invalid`, re-renders the page with `No one in Verify Org uses <email>.` under the field. An existing member gives `<email> is already in this team.`
- **Remove a member.** Run `aqv click --role button --name "Remove user@verify.invalid from the team"`. The dialog asks `Remove user@verify.invalid?`. Run `aqv click --role button --name Remove --exact`. The page reads `user@verify.invalid removed.`
- **Service account.** Choose `Add service account`, fill `Name`, and choose `Create service account`. The team page shows `Your key is ready` with the secret. The row buttons are `Rotate <name>`, `Edit <name>`, `Transfer <name>`, and `Delete <name>`. Rotate shows `Your key is ready` again. Transfer opens a form with the select `New owner`, listing the other team members, and the button `Transfer ownership`. Above 50 members, `New owner` is an email field, and an email of someone outside the team gives `<email> is not a member of Verify Team.` Rotate and Delete ask in the confirm dialog first. Delete names what goes with the account.
- **Appoint a team admin.** Run `aqv login org-admin --next /aqueduct/management/team/1`, then `aqv click --role button --name "Make team-member@verify.invalid a team admin"`. The page shows `team-member@verify.invalid is now a team admin.` The button `Remove admin rights of team-member@verify.invalid` takes the role away.
- **Set team limits.** As `org-admin`, open `/aqueduct/management/team/1/edit/`. Fill `Requests per minute, per key` and choose `Save changes`. The Limits card on the team page shows the value. Read it with `aqv shell -c "from management.models import Team; print(Team.objects.get(id=1).requests_per_minute)"`. As `team-admin`, the same form has no limit fields. As `other-org-admin`, the URL is a 404.
- **Delete a team.** As `org-admin`, open a team's `Edit` page and choose `Delete team` at the bottom. The dialog names what goes with the team.
- **Refused outside the team.** Run `aqv login user --next /aqueduct/management/team/1`. The page is the 404 page and does not name the team. `other-org-admin` gets the same 404 on every team and service account URL, such as `/team/1/delete/` and `/service-accounts/1/edit/`.
- **Refused inside the team.** As `team-member`, open `/aqueduct/management/team/1/edit/`. The browser lands on the team page with the one message `Only team admins can do that.` As `team-admin`, opening `/team/1/delete/` gives `Only org admins can do that.`

## Gotchas

- The seed has too few members and teams to show the pagers. They appear only past 50 rows. Add rows with `aqv shell` to see them, and say in the evidence that you did.

- A team or service account the viewer may not open returns 404. A member who lacks the rights is redirected to the team page with one message. Prove a refusal by the status or the message, and by reading the database with `aqv shell`.
- Signed-out requests to every team route redirect to the login page. Check them with `curl` against the base URL, since `aqv login` always signs someone in.
- Team admins cannot appoint other team admins. Only org admins and admins see `Make admin`, `Remove admin`, the limit fields, and `Delete team`.
- The token figures on both pages count only service account keys. Members' personal keys do not count toward the team.
- The `Edit` link next to the team limits opens the Edit team form and appears for org admins and admins.
- Without limits on the team or the org, the Limits card shows only to org admins and admins. Set a limit to see it as a member.
