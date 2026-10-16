# API keys

A signed-in user sees their personal API keys, creates a new one and sees its secret once, renames it, rotates it, and deletes it. Each user may hold three personal keys. Service account keys of the user's teams appear below the personal keys.

## Sub-features

- `keys-list` shows each personal key with its name, creation date, key preview, expiry, and limits.
- `keys-create` creates a key and shows the full secret once.
- `keys-limit` stops a user at three personal keys.
- `keys-edit` renames a key or changes its expiry.
- `keys-rotate` replaces a key's secret and shows the new one once.
- `keys-delete` deletes a key after a confirmation page.
- `keys-scope` refuses every action on another user's key.

## How to get to it (user POV)

- Choose **API Keys** in the top navigation. The path is `/aqueduct/management/tokens/`.
- Signing in lands on the same page.
- Choose **Create New API Key** on the API keys page.

## Driving it with aqv

Preconditions:

- A fresh launch. `user` holds one key, `user-laptop`.

- **List.** Run `aqv login user`. The page shows the heading `Your API Keys (1/3)` and a card with `user-laptop` and `Key: sk-...`.
- **Create.** Run `aqv click --role link --name "Create New API Key"`, then `aqv fill --label "API Key Name" --value "proof-key"`, then `aqv click --role button --name "Create API Key"`. The page returns to `/aqueduct/management/tokens/`, shows `API key 'proof-key' created successfully.`, shows the full secret starting with `sk-`, and the heading reads `Your API Keys (2/3)`. Run `aqv shot api-keys/create-user`.
- **Stored value.** Run `aqv shell -c "from management.models import Token; t = Token.objects.get(name='proof-key'); print(t.user.username, t.service_account_id, t.key_preview)"`. It prints `user None` and the same preview the page shows.
- **Edit.** Open `/aqueduct/management/tokens/<id>/edit/` for the key's ID, fill `API Key Name`, and submit. The card shows the new name.
- **Rotate.** Click the `Regenerate` button on the key's card. The page shows `has been regenerated` and a new secret. The preview in the card changes.
- **Delete.** Open `/aqueduct/management/tokens/<id>/delete/`. The page shows the heading `Confirm Deletion`. Run `aqv click --role button --name "Confirm Delete"`. The card is gone and the heading count drops by one.
- **Limit.** Create keys until the heading reads `Your API Keys (3/3)`. `Create New API Key` then is a disabled button instead of a link.
- **Scope.** Sign in as `team-member` and open `/aqueduct/management/tokens/<id>/edit/` for a key of `user`. The status is 404. Read the key's name with `aqv shell` to show it did not change.

## Gotchas

- The secret appears only once, in the message after create or rotate. A later page load shows only `sk-...` and the last four characters.
- Every card's icon buttons share the names `Regenerate`, `Edit`, and `Delete`. With more than one key, use `--nth` or open the key's own URL.
- Service account keys appear under `Service Accounts` on this page for team members, but the delete and edit routes on this page refuse them. Manage them from the team page.
- The three-key limit counts personal keys only.
