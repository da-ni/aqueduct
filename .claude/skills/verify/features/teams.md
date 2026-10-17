# Teams

Users see the teams they may open on the Teams page, with members, service accounts, the request limit, and seven days of service account tokens. A team's page shows seven-day totals, its service accounts, its members, and its limits. Team admins manage members and service accounts. Org admins also create and delete teams and appoint team admins.

## Sub-features

- `teams-list` lists the teams a user may see: their own with their role, or every team in the org for an org admin. A search box filters by team name. The list shows 50 teams per page. Admins see the org they chose in the sidebar, as [org-switch.md](./org-switch.md) shows.
- `team-detail` shows a team's totals, service accounts with owner, tokens, last use, and expiry, its members with their role (team admins first, 50 per page, `?q=` searches emails), and its limits and switched-off models.
- `team-members` adds and removes members.
- `team-admins` makes a member a team admin or removes the role.
- `team-service-accounts` creates, rotates, edits, transfers, and deletes service accounts.
- `team-create-delete` creates and deletes teams.
- `team-scope` refuses team pages and actions outside the user's teams or org.

## How to get to it (user POV)

- Choose **Teams** in the sidebar. The path is `/aqueduct/management/org/`.
- Choose a team name in the table. The path is `/aqueduct/management/team/<id>`. The seeded `Verify Team` has ID 1.
- Choose **Create team** on the Teams page, as an org admin.

## Driving it with aqv

Preconditions:

- A fresh launch. `Verify Team` has the team admin `team-admin`, the member `team-member`, and the service account `verify-pipeline` owned by `team-admin`.

- **List as a member.** Run `aqv login team-member --next /aqueduct/management/org/`. The table shows `Verify Team` with `Member` under `Your role`, `2` members, and `1` service account.
- **List as org admin.** Run `aqv login org-admin --next /aqueduct/management/org/`. The page shows a `Create team` link and no `Your role` column.
- **Create a team.** As `org-admin`, run `aqv click --role link --name "Create team"`, `aqv fill --label Name --value "Proof Team"`, then `aqv click --role button --name "Create team"`. The page returns to the Teams page with `Proof Team` in the table.
- **Detail as team admin.** Run `aqv login team-admin --next /aqueduct/management/team/1`. The page shows `Edit`, `Add service account`, the `Service accounts` table with `verify-pipeline`, and `Members`. The remove button `Remove team-admin@verify.invalid from the team` is disabled because that user owns a service account here.
- **Add a member.** On the same page, run `aqv fill --label "Email of someone in Verify Org to add" --value user@verify.invalid` and `aqv click --role button --name "Add member"`. `user@verify.invalid` appears under `Members`. An email outside `Verify Org`, such as `other-org-admin@verify.invalid`, re-renders the page with `No one in Verify Org uses <email>.` under the field. An existing member gives `<email> is already in this team.`
- **Remove a member.** Run `aqv click --role button --name "Remove user@verify.invalid from the team"`. The dialog asks `Remove user@verify.invalid?`. Run `aqv click --role button --name Remove --exact`. The page reads `user@verify.invalid removed.`
- **Service account.** Choose `Add service account`, fill `Name`, and choose `Create service account`. The team page shows `Your key is ready` with the secret. The row buttons are `Rotate <name>`, `Edit <name>`, `Transfer <name>`, and `Delete <name>`. Rotate shows `Your key is ready` again. Transfer opens a form with the email field `New owner` and the button `Transfer ownership`. An email of someone outside the team gives `<email> is not a member of Verify Team.` Rotate and Delete ask in the confirm dialog first. Delete names what goes with the account.
- **Appoint a team admin.** Run `aqv login org-admin --next /aqueduct/management/team/1`, then `aqv click --role button --name "Make team admin"`. The page shows `team-member@verify.invalid is now a team admin.`
- **Refused outside the team.** Run `aqv login user --next /aqueduct/management/team/1`. The page is the 404 page and does not name the team. `other-org-admin` gets the same 404 on every team and service account URL, such as `/team/1/delete/` and `/service-accounts/1/edit/`.
- **Refused inside the team.** As `team-member`, open `/aqueduct/management/team/1/edit/`. The browser lands on the team page with the one message `Only team admins can do that.` As `team-admin`, opening `/team/1/delete/` gives `Only org admins can do that.`

## Gotchas

- The seed has too few members and teams to show the pagers. They appear only past 50 rows. Add rows with `aqv shell` to see them, and say in the evidence that you did.

- A team or service account the viewer may not open returns 404. A member who lacks the rights is redirected to the team page with one message. Prove a refusal by the status or the message, and by reading the database with `aqv shell`.
- Signed-out requests to every team route redirect to the login page. Check them with `curl` against the base URL, since `aqv login` always signs someone in.
- Team admins cannot appoint other team admins. Only org admins and admins see `Make team admin` and `Delete`.
- The token figures on both pages count only service account keys. Members' personal keys do not count toward the team.
- The `Edit` link next to the default limit opens the organization's limits form and appears for org admins and admins. Staff users, such as `admin`, also get `Edit in admin` there. The `Edit` link next to the team limits opens the Django admin and appears only for staff users, such as `admin`.
