# Settings

Every signed-in user has a Settings page. It shows the account the user signed in with, the organization, the role, and the teams the user is in with the role in each. It shows when the user accepted the current terms of service and the limits of the user's personal keys with how many keys the user has. The page changes no stored data. Sign out is the only form on it.

## Sub-features

- `settings-account` shows the rows `Email`, `Organization` with the note `Set by your sign-in provider.`, `Role`, and `Teams`, each team with `Team admin` or `Member`, the labels the team page uses, or `None`. A team name links to the team page.
- `settings-terms` shows `Accepted <date>` or `Not accepted`. It appears only when the terms of service are switched on.
- `settings-limits` is the `Personal key limits` card. It shows the requests, input tokens, and output tokens per minute for personal keys, the count of personal keys against the maximum, and the note `Organization default`, or `Set for your account. Others use the organization default.` when the account has limits of its own.

## How to get to it (user POV)

- Choose **Settings** at the bottom of the sidebar. The path is `/aqueduct/management/settings/`.

## Driving it with aqv

Preconditions:

- A fresh launch.

- **Account as a member.** Run `aqv login team-member --next /aqueduct/management/settings/`. The page shows `team-member@verify.invalid`, `Verify Org`, the role `Member`, and `Verify Team` followed by `Member`. Run `aqv shot settings/team-member`.
- **Team role.** Run `aqv login team-admin --next /aqueduct/management/settings/`. `Verify Team` is followed by `Team admin`. Run `aqv click --role link --name "Verify Team" --exact`. The browser lands on `/aqueduct/management/team/1`.
- **No team.** Run `aqv login user --next /aqueduct/management/settings/`. `Teams` shows `None`, and `Personal keys` shows `1 of 3`.
- **Roles.** `org-admin` shows `Org admin`. `admin` shows `Platform admin` and `0 of 3` personal keys.
- **Limits from the account.** Run `aqv shell -c "from management.models import UserProfile; p = UserProfile.objects.get(user__username='user'); p.requests_per_minute = 30; p.save()"`, then `aqv login user --next /aqueduct/management/settings/`. `Requests per minute` shows `30` and the note says `Set for your account. Others use the organization default.`
- **Sign out.** Run `aqv click --selector 'main button[type=submit]'`. The browser lands on `/login/`.

## Gotchas

- The seed sets no limits, so every limit reads `No limit` until you set one.
- The theme switch is in the sidebar, not on this page.
- Signed-out requests redirect to the login page. Check them with `curl` against the base URL.
