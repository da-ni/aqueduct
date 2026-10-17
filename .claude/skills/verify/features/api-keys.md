# API keys

A signed-in user sees their personal API keys and the service account keys of their teams in one table with seven days of usage. They create a key and see its secret once, open a key's detail page, rename it, rotate it, and delete it. Each user may hold three personal keys. Service account keys are managed from the team page.

## Sub-features

- `keys-list` shows each key with its name, type, key preview, last use, tokens over seven days, cache hit share, request count, and expiry. Team members get the tabs `All mine`, `Personal`, and `Service accounts`. A search box filters by key or team name.
- `keys-detail` shows one personal key: seven-day totals, tokens or requests per day, the models it calls, its clients, the latest requests, and its limits.
- `keys-create` creates a key and shows the full secret once, with a copyable `curl` command.
- `keys-limit` stops a user at three personal keys.
- `keys-edit` renames a key or changes its expiry.
- `keys-rotate` replaces a key's secret and shows the new one once.
- `keys-delete` deletes a key after a confirmation page.
- `keys-scope` refuses every action and the detail page on another user's key and on service account keys.

## How to get to it (user POV)

- Choose **API keys** in the sidebar. The path is `/aqueduct/management/tokens/`.
- Choose a key's name in the table to open its detail page at `/aqueduct/management/tokens/<id>/`. A service account's name opens its team page instead.
- Choose **Create API key** on the API keys page.

## Driving it with aqv

Preconditions:

- A fresh launch. `user` holds one key, `user-laptop`, with ID 1. `team-admin` owns the service account key `verify-pipeline`, ID 5.

- **List.** Run `aqv login user --next /aqueduct/management/tokens/`. The page shows the heading `API keys`, the text `1 of 3 personal keys`, and a row for `user-laptop` with `Personal · sk-...`.
- **Detail.** Run `aqv click --role link --name user-laptop --exact`. The path is `/aqueduct/management/tokens/1/`, and the page shows `Input tokens`, `Tokens per day`, `Models it calls`, `Latest requests`, and `Limits`. Run `aqv shot api-keys/detail-user`.
- **Create.** From the list, run `aqv click --role link --name "Create API key"`, then `aqv fill --label Name --value proof-key`, then `aqv click --role button --name "Create key"`. The page returns to `/aqueduct/management/tokens/`, shows `Your key is ready` with the full secret starting with `sk-`, and reads `2 of 3 personal keys`. Run `aqv shot api-keys/create-user`.
- **Stored value.** Run `aqv shell -c "from management.models import Token; t = Token.objects.get(name='proof-key'); print(t.user.username, t.service_account_id, t.key_preview)"`. It prints `user None` and the same last four characters the secret ends with.
- **Call the gateway.** With the secret from the create step, call `/v1/models` and `/v1/chat/completions` as `../SKILL.md` describes under `Call the gateway`. Both return 200.
- **Edit.** Run `aqv click --role link --name "Edit proof-key"`, fill `Name`, and choose `Save changes`. The table shows the new name.
- **Rotate.** On the key's detail page, run `aqv click --role button --name Rotate`. The list shows `Your key is ready` with a new secret, and the preview in the table changes. On the list, the row button is `Rotate <key name>`.
- **Delete.** Run `aqv click --role link --name "Delete <key name>"`. The page shows the heading `Confirm Deletion`. Run `aqv click --role button --name "Confirm Delete"`. The row is gone and the count drops by one.
- **Limit.** Create keys until the page reads `3 of 3 personal keys`. `Create API key` then is a disabled button instead of a link.
- **Scope.** Sign in as `team-member` and open `/aqueduct/management/tokens/1/` and `/aqueduct/management/tokens/1/edit/`. Both return 404. Sign in as `team-admin` and open `/aqueduct/management/tokens/5/`. It returns 404 too. Read the key's name with `aqv shell` to show it did not change.

## Gotchas

- The secret appears only once, in the `Your key is ready` panel after create or rotate. A later page load shows only `sk-...` and the last four characters.
- The key's name link, `Edit <name>`, and `Delete <name>` all match `--name <name>` without `--exact`.
- Service account rows show `Managed by the team` instead of buttons. Rotate, edit, and delete them on the team page, where the buttons are `Rotate <name>`, `Edit <name>`, and `Delete <name>`. Rotating there also shows the `Your key is ready` panel.
- The tabs appear only for a user in at least one team that has a service account.
- The three-key limit counts personal keys only.
