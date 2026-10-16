# Teams

Users see the teams they may open on the Teams page, with members, service accounts, the request limit, and seven days of service account tokens. A team's page shows seven-day totals, its service accounts, its members, and its limits. Team admins manage members and service accounts. Org admins also create and delete teams and appoint team admins.

## Sub-features

- `teams-list` lists the teams a user may see: their own with their role, or every team in the org for an org admin. A search box filters by team name. Admins switch the org there.
- `team-detail` shows a team's totals, service accounts with owner, tokens, last use, and expiry, its members with their role, and its limits and switched-off models.
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
- **Add a member.** On the same page, read the profile ID with `aqv shell -c "from management.models import UserProfile; print(UserProfile.objects.get(user__username='user').id)"`, then run `aqv select --label "Add someone from Verify Org" --value <id>` and `aqv click --role button --name "Add member"`. `user@verify.invalid` appears under `Members`.
- **Remove a member.** Run `aqv click --role button --name "Remove user@verify.invalid from the team"`.
- **Service account.** Choose `Add service account`, fill `Name`, and choose `Create service account`. The team page shows `Your key is ready` with the secret. The row buttons are `Rotate <name>`, `Edit <name>`, `Transfer <name>`, and `Delete <name>`. Rotate shows `Your key is ready` again. Transfer opens a form with the select `New owner` and the button `Transfer ownership`. Delete opens `Confirm Deletion`, then `Confirm Delete`.
- **Appoint a team admin.** Run `aqv login org-admin --next /aqueduct/management/team/1`, then `aqv click --role button --name "Make team admin"`. The page shows `team-member@verify.invalid is now a team admin for Verify Team.`
- **Refused for a plain user.** Run `aqv login user --next /aqueduct/management/team/1`. The browser lands on `/aqueduct/management/org/` with the message that the user may not view the team.

## Gotchas

- A refused team action redirects with a message. It does not return 403. Prove a refusal by the message and by reading the database with `aqv shell`.
- On `main` at the time of writing, an org admin of another org can delete a team and appoint team admins by ID. Use `other-org-admin` against team 1 to check a fix for that.
- Signed-out requests to every team route redirect to the login page. Check them with `curl` against the base URL, since `aqv login` always signs someone in.
- Team admins cannot appoint other team admins. Only org admins and admins see `Make team admin` and `Delete`.
- The token figures on both pages count only service account keys. Members' personal keys do not count toward the team.
- The `Edit` link next to the default limit opens the organization's limits form and appears for org admins and admins. The `Edit` link next to the team limits opens the Django admin and appears only for staff users, such as `admin`.
