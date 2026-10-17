# Settings

Every signed-in user has a Settings page. It shows the account the user signed in with, the organization, the role, and the teams the user is in with the role in each. It shows when the user accepted the current terms of service, a light and dark switch, and the limits of the user's personal keys with how many keys the user has. The page changes no stored data. Sign out is the only form on it.

## Sub-features

- `settings-account` shows the e-mail address, the sign-in provider name, the organization, the role, and each team with `Team admin` or `Member`, the labels the team page uses. A team name links to the team page.
- `settings-terms` shows the date the user accepted the current terms, or that the user has not. It appears only when the terms of service are switched on.
- `settings-appearance` switches between light and dark. The browser stores the choice, not the server.
- `settings-limits` shows the requests, input tokens, and output tokens per minute for personal keys, the count of personal keys against the maximum, and whether an admin set limits for the account.

## How to get to it (user POV)

- Choose **Settings** at the bottom of the sidebar. The path is `/aqueduct/management/settings/`.

## Driving it with aqv

Preconditions:

- A fresh launch.

- **Account as a member.** Run `aqv login team-member --next /aqueduct/management/settings/`. The page shows `team-member@verify.invalid`, `Verify Org`, the role `User`, and `Verify Team` followed by `Member`. Run `aqv shot settings/team-member`.
- **Team role.** Run `aqv login team-admin --next /aqueduct/management/settings/`. `Verify Team` is followed by `Team admin`. Run `aqv click --role link --name "Verify Team" --exact`. The browser lands on `/aqueduct/management/team/1`.
- **No team.** Run `aqv login user --next /aqueduct/management/settings/`. The page shows `You are in no team.` and `Personal keys` `1 of 3`.
- **Roles.** `org-admin` shows `Org Admin`. `admin` shows `Admin` and `0 of 3` personal keys.
- **Appearance.** Run `aqv click --role button --name Dark --exact`, then `aqv open /aqueduct/management/settings/` and `aqv aria --selector 'section[aria-labelledby=appearance-title]'`. The snapshot shows `button "Dark" [pressed]`. Choose `Light` the same way to switch back.
- **Limits from the account.** Run `aqv shell -c "from management.models import UserProfile; p = UserProfile.objects.get(user__username='user'); p.requests_per_minute = 30; p.save()"`, then `aqv login user --next /aqueduct/management/settings/`. `Requests per minute` shows `30` and the note says an admin set limits for the account.
- **Sign out.** Run `aqv click --selector 'main button[type=submit]'`. The browser lands on `/login/`.

## Gotchas

- The seed sets no limits, so every limit reads `No limit` until you set one.
- `Sign-in` shows the configured provider name, which is `SSO` on a verification instance.
- The sidebar also has a button named `Switch between light and dark`. Pass `--exact` when you name `Dark` or `Light`.
- The theme lives in the browser's local storage, so it survives `aqv login` as another account.
- Signed-out requests redirect to the login page. Check them with `curl` against the base URL.
