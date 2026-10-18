# Aqueduct management UI verification map

This directory is the maintained source for verifying what users see and do in the management UI. Read this index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch a private instance with `.claude/skills/verify/scripts/aqv launch`, as `../SKILL.md` describes.
- Run `aqv doctor` and require exit code 0.
- The seed gives `Verify Org` with `Verify Team` (team admin `team-admin`, member `team-member`), the service account `verify-pipeline`, the personal keys `user-laptop`, `member-notebook`, `team-admin-cli`, and `org-admin-scripts`, 280 requests over the last 7 days, and files, batches, and vector stores for `user` and `verify-pipeline`.
- Never drive `aqueduct-dev.service` on port 8085.

## Driving conventions

- Start every recipe from a fresh launch unless it says otherwise. Recipes change data, and the seed runs only once per launch.
- Sign in with `aqv login <username>`. Each recipe names the account.
- Name elements by ARIA role and accessible name, as the recipes do. The icon buttons on the API keys page and the team page carry the item's name, such as `Rotate user-laptop` or `Delete verify-pipeline`.
- Delete, Rotate, batch Cancel, and member removal open the shared confirm dialog, `<dialog id="confirm-dialog">`. Choose its action button with `aqv click --role button --name <Delete|Rotate|Remove|"Cancel batch"> --exact`, or the dismiss button, `Cancel` (`Keep running` in the batch dialog). The question and consequence are in `aqv text --selector '#confirm-dialog'`. The browser still accepts every `confirm()` dialog, which the team admin buttons use.
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

- [API keys](./api-keys.md) covers the key table, the key detail page, creating, editing, rotating, and deleting keys, service account actions on the list, org admin read-only access, and the per-user limit.
- [Organization switch](./org-switch.md) covers the admin's org select in the sidebar, which every org-scoped page follows.
- [Organization](./organization.md) covers the organization page, editing the org's limits and available models and MCP servers, and who may do it.
- [Teams](./teams.md) covers the team list, team members, team admins, and service accounts, and who may see or change each.
- [Overview](./usage.md) covers the usage dashboard, the All keys page, their filters, and their role scoping.
- [Requests](./requests.md) covers the request log, its status, key, and model filters, the row details, and the pages.
- [Models](./models.md) covers the model table, its type tabs and search, the detail panel, and which keys may use a model.
- [Settings](./settings.md) covers the account details, team roles, terms acceptance, the light and dark switch, and the personal key limits.
- [Terms of service](./terms-of-service.md) covers the prompt a user sees before accepting the terms.
- [Data protection information](./privacy.md) covers the public data protection page, the per-organization notice, and the empty state.
- [Files, batches, and vector stores](./resources.md) covers the three resource lists, the file preview, file delete, batch cancel, and the vector store detail page.
- [MCP servers](./mcp-servers.md) covers the server cards, their URLs, which keys may use them, and the client configuration.
