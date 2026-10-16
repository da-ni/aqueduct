# Models

The Models page lists every model in the router config in a table with its type, plus a Context column when any model has a context window. Type tabs and a search over names and aliases filter the table. A detail panel shows the selected model: its type, its name with a copy button, the spec cells the config has, the served model, its aliases, its capabilities, and a `curl` example for its endpoint. A model that is switched off for one of the viewer's scopes, their personal keys or the service accounts of a team they belong to, carries an `OFF FOR <scope>` badge, and its panel says where it is off and where it is still available. The gateway URL has a copy button.

## Sub-features

- `models-list` shows one row per model, in router config order, with its type.
- `models-filter` filters the rows with the type tabs. A tab appears only for a type that some model has, and the tabs appear only when there are two types or more.
- `models-search` matches the search text against model names and aliases. The field submits on Enter.
- `models-detail` shows the selected model. A row link or `?model=<name or alias>` selects it, and the first listed model is selected otherwise.
- `models-switch-off` shows the badge and the notice for a model that is switched off in one of the viewer's scopes. Staff also get an `Edit <scope> in admin` link per scope.
- `models-copy` copies the gateway URL, the model name, and the `curl` example.

## How to get to it (user POV)

- Choose **Models** in the sidebar. The path is `/aqueduct/management/models/`.
- The URL holds the page state. `?type=images`, `?q=stt`, and `?model=tts` open a tab, a search, and a selected model.

## Driving it with aqv

Preconditions:

- A fresh launch. The router config is `example_router_config.yaml` from the main checkout. It gives every model aliases and a mode and gives `text-embedding-ada-002` a request limit multiplier of 2. It has no context windows, so the table has no Context column.

- **List.** Run `aqv login user --next /aqueduct/management/models/`. The table lists `gpt-4.1-nano`, `text-embedding-ada-002`, `gpt-4o-mini-tts`, `whisper-1`, `dall-e-2`, and `gpt-image-1`. The panel shows `gpt-4.1-nano` with a `curl` example for `<base>/v1/chat/completions`. Run `aqv shot models/list-user`.
- **Detail.** Run `aqv click --role link --name text-embedding-ada-002`. The panel shows `EMBEDDINGS`, the served model `openai/text-embedding-ada-002`, the aliases `embedding` and `embed`, the capability `2x request limit`, and a `curl` example for `/v1/embeddings`. Run `aqv shot models/detail-embedding-user`.
- **Filter.** Run `aqv click --role link --name Images --exact`. The URL ends in `?type=images`, and the table lists only `dall-e-2` and `gpt-image-1`.
- **Search.** aqv cannot press Enter, so open the search by its URL: `aqv open "/aqueduct/management/models/?q=stt"`. The table lists only `whisper-1`, which matches by its alias `stt`.
- **Switch-off.** Switch-offs are edited in the Django admin. For a quick precondition, run `aqv shell -c "from management.models import Team; t = Team.objects.get(name='Verify Team'); t.excluded_models = ['gpt-image-1']; t.save()"`. Then run `aqv login team-member --next "/aqueduct/management/models/?model=gpt-image-1"`. The row shows `OFF FOR VERIFY TEAM`, and the panel says `Switched off for Verify Team service accounts.` and `Still available for your personal keys.` It has no admin link. Run `aqv shot models/switch-off-team-member`. As `user`, who is in no team, the same page shows no badge and no notice.
- **Admin link.** `admin` is in no team, so it sees no badge either. Run `aqv shell -c "from management.models import Team, TeamMembership, UserProfile; TeamMembership.objects.create(user_profile=UserProfile.objects.get(user__username='admin'), team=Team.objects.get(name='Verify Team'))"`, then `aqv login admin --next "/aqueduct/management/models/?model=gpt-image-1"`. The panel has the link `Edit Verify Team in admin`. Run `aqv click --role link --name "Edit Verify Team in admin"`. The title is `Verify Team (Verify Org) | Change team | Django site admin`.
- **Cross-check.** Run `aqv shell -c "from gateway.config import get_router_config; print([m['model_name'] for m in get_router_config()['model_list']])"`. Every name has a row, in the same order.

## Gotchas

- A scope is where the viewer's keys live: their personal keys, and each team they are a member of. Admins and org admins see a team's switch-off only when they are members of that team.
- The example config has no context window, output limit, dimensions, modalities, capability flags, or deprecation date, so those cells, the Context column, and the `DEPRECATED` badge do not appear. To see them, write a router config that has those `model_info` keys and the same six model names, and set `LITELLM_ROUTER_CONFIG_FILE_PATH` to it for `aqv launch` and for every `aqv shell` call on that run.
- The server reads the router config once. After you change the config, launch a new run.
- The copy buttons write to the clipboard, which headless Chromium does not expose. Check the copied text in the page, not the clipboard.
