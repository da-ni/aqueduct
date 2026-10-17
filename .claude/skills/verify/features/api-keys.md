# API keys

A signed-in user sees their personal API keys and the service account keys of their teams in one table with seven days of usage. They create a key and see its secret once, open a key's detail page, rename it, rotate it, and delete it. Each user may hold three personal keys. Team admins rotate, edit, and delete their teams' service accounts from the same table. Org admins open every key of their org read-only, and admins every key.

## Sub-features

- `keys-list` shows each key with its name, type, key preview, last use (`—` when never), tokens over seven days, request count, and expiry (`No expiry` when none). Team members get the tabs `All`, `Personal`, and `Service accounts`. A search box filters by key or team name. With no key and no filter, the table reads `No keys yet.` with a `Create` button. Below the `md` breakpoint only the key, the tokens, and a `⋯` button (`Actions for <name>`) show; the button opens `Rotate`, `Edit`, and `Delete`.
- `keys-detail` shows one key: `Last 7 days` totals, tokens or requests per day, `Models`, its clients, the latest requests with one `All requests` link, and `Limits`, which read `No limits` when none is set and otherwise list only the set ones.
- `keys-create` creates a key and shows the full secret once, with `Copy it now. You won't see it again.` and a copyable snippet that exports `OPENAI_BASE_URL` and `OPENAI_API_KEY` and runs `curl $OPENAI_BASE_URL/models`. No toast shows next to the panel, after create or rotate.
- `keys-limit` stops a user at three personal keys. The create button reads `Create API key` with `1/3`, and at the limit is a disabled `3 of 3 keys used`.
- `keys-edit` renames a key or changes its expiry.
- `keys-rotate` replaces a key's secret and shows the new one once.
- `keys-delete` deletes a key after the confirm dialog. Without JavaScript the link opens a confirm page that asks the same question.
- `keys-scope` shows a key's page to its owner, to members of a service account's team, to org admins for every key of their org, and to admins for every key. Only the owner of a personal key, or a team admin for a service account, gets `Rotate`, `Edit`, and `Delete`; everyone else gets 404 on those actions.

## How to get to it (user POV)

- Choose **API keys** in the sidebar. The path is `/aqueduct/management/tokens/`.
- Choose a key's name in the table to open its detail page at `/aqueduct/management/tokens/<id>/`. A service account's name opens its team page instead.
- Choose **Create API key** on the API keys page.
- An org admin opens another member's key at `/aqueduct/management/tokens/<id>/`. Its breadcrumb is `All keys`, and the subtitle reads `Personal · <owner email>`.

## Driving it with aqv

Preconditions:

- A fresh launch. `user` holds one key, `user-laptop`, with ID 1. `team-admin` owns the service account key `verify-pipeline`, ID 5.

- **List.** Run `aqv login user --next /aqueduct/management/tokens/`. The page shows the heading `API keys`, the button `Create API key 1/3`, and a row for `user-laptop` with `Personal · sk-...`.
- **Detail.** Run `aqv click --role link --name user-laptop --exact`. The path is `/aqueduct/management/tokens/1/`, and the page shows `Last 7 days`, `Input tokens`, `Tokens per day`, `Models`, `Latest requests`, `All requests`, and `Limits`. Run `aqv shot api-keys/detail-user`.
- **Create.** From the list, run `aqv click --role link --name "Create API key"`, then `aqv fill --label Name --value proof-key`, then `aqv click --role button --name Create --exact`. The page returns to `/aqueduct/management/tokens/`, shows `Your key is ready` with the full secret starting with `sk-` and no toast, and the button reads `Create API key 2/3`. Run `aqv shot api-keys/create-user`.
- **Stored value.** Run `aqv shell -c "from management.models import Token; t = Token.objects.get(name='proof-key'); print(t.user.username, t.service_account_id, t.key_preview)"`. It prints `user None` and the same last four characters the secret ends with.
- **Call the gateway.** With the secret from the create step, call `/v1/models` and `/v1/chat/completions` as `../SKILL.md` describes under `Call the gateway`. Both return 200.
- **Edit.** Run `aqv click --role link --name "Edit proof-key"`, fill `Name`, and choose `Save`. The table shows the new name.
- **Rotate.** On the key's detail page, run `aqv click --role button --name Rotate`. The list shows `Your key is ready` with a new secret, and the preview in the table changes. On the list, the row button is `Rotate <key name>`.
- **Delete.** Run `aqv click --role link --name "Delete <key name>"`. The dialog asks `Delete <key name>?` with `Apps using it stop working, and its requests, files, batches, and vector stores are deleted.` Run `aqv click --role button --name Delete --exact`. The page reads `API key <key name> deleted.`, the row is gone, and the count on `Create API key` drops by one. `aqv open /aqueduct/management/tokens/<id>/delete/` shows the same question as a page.
- **Limit.** Create keys until the page reads `3 of 3 keys used`. That is a disabled button instead of the `Create API key` link.
- **Scope.** Sign in as `team-member` and open `/aqueduct/management/tokens/1/` and `/aqueduct/management/tokens/1/edit/`. Both return 404. Sign in as `org-admin` and open `/aqueduct/management/tokens/1/`. It returns 200 with no `Rotate`, `Edit`, or `Delete`, and `/aqueduct/management/tokens/1/edit/` returns 404. `other-org-admin` gets 404 on `/aqueduct/management/tokens/1/`. Read the key's name with `aqv shell` to show it did not change.
- **Service accounts on the list.** As `team-admin`, the `verify-pipeline` row has `Rotate verify-pipeline`, `Edit verify-pipeline` (the service account form), and `Delete verify-pipeline`. Rotating there returns to the list with the new secret. As `team-member`, the row has no buttons.
- **Phone width.** `aqv` fixes the viewport at 1440 px. For 390 px, connect Playwright to the run's `cdp_port` from `state.json`, call `page.set_viewport_size({"width": 390, "height": 900})`, and click `Actions for <name>`. The theme is `localStorage.selectedTheme` (`light` or `dark`); set it and reload.

## Gotchas

- The secret appears only once, in the `Your key is ready` panel after create or rotate. A later page load shows only `sk-...` and the last four characters.
- The key's name link, `Edit <name>`, and `Delete <name>` all match `--name <name>` without `--exact`.
- Each row's actions exist twice in the page: icon buttons for `md` and up, and the `⋯` menu below it. Add `--nth 0` or match only the visible one when a name is ambiguous.
- Service account actions on the list show only for team admins of that team. The team page has the same buttons.
- The tabs appear only for a user in at least one team that has a service account.
- The three-key limit counts personal keys only.
