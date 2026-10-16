# Teams

Users see the teams of their org on the Teams page and open a team to see its limits, service accounts, and members. Team admins manage members and service accounts. Org admins also create and delete teams and appoint team admins.

## Sub-features

- `teams-list` lists the teams a user may see: their own, or every team in the org for an org admin.
- `team-detail` shows a team's limits, service accounts, members, and the users who could be added.
- `team-members` adds and removes members.
- `team-admins` makes a member a team admin or removes the role.
- `team-service-accounts` creates, edits, rotates, transfers, and deletes service accounts.
- `team-create-delete` creates and deletes teams.
- `team-scope` refuses team pages and actions outside the user's teams or org.

## How to get to it (user POV)

- Choose **Teams** in the sidebar. The path is `/aqueduct/management/org/`.
- Choose a team in the `Teams` list. The path is `/aqueduct/management/team/<id>`. The seeded `Verify Team` has ID 1.
- Choose **Create New Team** on the Teams page, as an org admin.

## Driving it with aqv

Preconditions:

- A fresh launch. `Verify Team` has the team admin `team-admin`, the member `team-member`, and the service account `verify-pipeline` owned by `team-admin`.

- **List as org admin.** Run `aqv login org-admin --next /aqueduct/management/org/`. The page shows `Org Verify Org`, a `Create New Team` link, and the `Verify Team` entry with `2 Members`.
- **Detail as team admin.** Run `aqv login team-admin --next /aqueduct/management/team/1`. The page shows `Edit Team`, `Add Service Account`, the heading `Service Accounts (1/10)` with `verify-pipeline`, and `Current Members`. The `Remove` button for `team-admin@verify.invalid` is disabled because that user owns a service account here.
- **Add a member.** On the same page, run `aqv click --role button --name "Add to Team" --nth 2`. That is the third entry under `Add Users to Team`, `user@verify.invalid`. It moves to `Current Members`. Run `aqv shot teams/add-member-team-admin`.
- **Remove a member.** Click the enabled `Remove` button next to that member. The member returns to `Add Users to Team`.
- **Appoint a team admin.** Run `aqv login org-admin --next /aqueduct/management/team/1`, then `aqv click --role button --name "Make Team Admin"`. The page shows `team-member@verify.invalid is now a team admin for Verify Team.`
- **Refused for a plain user.** Run `aqv login user --next /aqueduct/management/team/1`. The browser lands on `/aqueduct/management/org/` with the message that the user may not view the team.

## Gotchas

- A refused team action redirects to the Teams page with a message. It does not return 403. Prove a refusal by the message and by reading the database with `aqv shell`.
- On `main` at the time of writing, an org admin of another org can delete a team and appoint team admins by ID. Use `other-org-admin` against team 1 to check a fix for that.
- A signed-out request to the team routes returns 500 on `main` at the time of writing. Check it with `curl` against the base URL, since `aqv login` always signs someone in.
- Team admins cannot appoint other team admins. Only org admins and admins see `Make Team Admin`.
