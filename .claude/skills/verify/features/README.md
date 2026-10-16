# Aqueduct management UI verification map

This directory is the maintained source for verifying what users see and do in the management UI. Read this index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch a private instance with `.claude/skills/verify/scripts/aqv launch`, as `../SKILL.md` describes.
- Run `aqv doctor` and require exit code 0.
- The seed gives `Verify Org` with `Verify Team` (team admin `team-admin`, member `team-member`), the service account `verify-pipeline`, the personal keys `user-laptop`, `member-notebook`, `team-admin-cli`, and `org-admin-scripts`, and 280 requests over the last 7 days.
- Never drive `aqueduct-dev.service` on port 8085.

## Driving conventions

- Start every recipe from a fresh launch unless it says otherwise. Recipes change data, and the seed runs only once per launch.
- Sign in with `aqv login <username>`. Each recipe names the account.
- Name elements by ARIA role and accessible name, as the recipes do. The icon buttons on the API keys page and the team page carry the item's name, such as `Rotate user-laptop` or `Delete verify-pipeline`.
- The browser accepts every `confirm()` dialog, so a click on a guarded button goes through.
- Read stored values with `aqv shell -c`.

## Proof and skip reporting

- Capture the user action and the resulting state with `aqv shot`, not only the final screen.
- For a change that saves data, read the stored value with `aqv shell`.
- For a permission rule, prove the allowed role and an out-of-scope role, and show that the database did not change for the refused one.
- Record the feature ID and the account with every artifact name, such as `api-keys/create-user.png`.
- Report an unreachable path with the command you ran and the precondition that was missing. Do not report a skipped path as verified through another one.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with aqv`, and `Gotchas`. Keep implementation details out. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [API keys](./api-keys.md) covers the key table, the key detail page, creating, editing, rotating, and deleting personal keys, and the per-user limit.
- [Teams](./teams.md) covers the team list, team members, team admins, and service accounts, and who may see or change each.
- [Usage](./usage.md) covers the usage dashboard, its time frames, and the org selector for admins.
- [Models](./models.md) covers the model table, its type tabs and search, the detail panel, and the notice for a switched-off model.
- [Settings](./settings.md) covers the account details, team roles, terms acceptance, the light and dark switch, and the personal key limits.
- [Terms of service](./terms-of-service.md) covers the prompt a user sees before accepting the terms.

Not mapped yet: Files, Batches, Vector stores, and MCP servers. Their pages load for every role, but the seed creates no files, batches, or vector stores, and those come from the OpenAI-compatible API, not the UI.
