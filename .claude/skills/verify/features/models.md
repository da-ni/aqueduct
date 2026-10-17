# Models

The Models page lists every model in the router config in a table. Each row shows the name, and under it the type and the `description` from `model_info`. The table adds an Aliases column (from 768 px), a Quant column (from 640 px), and a Context column, each only when some model has that value. Below those widths, the aliases and the quantization badge move under the name. Type tabs and a search over names and aliases filter the table. A detail panel shows the selected model: its type, its name with a copy button, its description, a `Model card` link, the spec cells, the served model, its aliases with a copy button each, its capabilities, a `curl` example for its endpoint, and links to its usage in the Overview and its requests in the log. A model that is switched off for one of the viewer's scopes, their personal keys or the service accounts of a team they belong to, carries an `OFF FOR <scope>` badge, and its panel says where it is off and where it is still available. The gateway URL has a copy button.

## Sub-features

- `models-list` shows one row per model, in router config order, with its type.
- `models-filter` filters the rows with the type tabs. A tab appears only for a type that some model has, and the tabs appear only when there are two types or more.
- `models-search` matches the search text against model names and aliases. The field submits on Enter.
- `models-detail` shows the selected model. A row link or `?model=<name or alias>` selects it, and the first listed model is selected otherwise.
- `models-switch-off` shows the badge and the notice for a model that is switched off in one of the viewer's scopes. Staff also get an `Edit <scope> in admin` link per scope.
- `models-details` shows the description, quantization, and token limits from `model_info`. A chat model always shows `Quantization`, `Max input tokens`, and `Max output tokens`, with `Not configured` for a missing value. An embedding model shows the first two. The `Model card` link opens `model_card_url`, or `https://huggingface.co/<org>/<name>` when the served model without its provider prefix, or the model name, is a Hugging Face repo ID. Without either, there is no link. The quantization falls back to the suffix of the repo name, such as `AWQ` or `FP8`.
- `models-usage` links the selected model to the Overview and the request log, both filtered by `?model=<name>`.
- `models-copy` copies the gateway URL, the model name, each alias, and the `curl` example.

## How to get to it (user POV)

- Choose **Models** in the sidebar. The path is `/aqueduct/management/models/`.
- The URL holds the page state. `?type=images`, `?q=stt`, and `?model=tts` open a tab, a search, and a selected model.

## Driving it with aqv

Preconditions:

- A fresh launch. The router config is `example_router_config.yaml` from the main checkout. It gives every model aliases and a mode and gives `text-embedding-ada-002` a request limit multiplier of 2. It has no context windows, so the table has no Context column.

- **List.** Run `aqv login user --next /aqueduct/management/models/`. The table lists `gpt-4.1-nano`, `text-embedding-ada-002`, `gpt-4o-mini-tts`, `whisper-1`, `dall-e-2`, and `gpt-image-1`. The panel shows `gpt-4.1-nano` with a `curl` example for `<base>/v1/chat/completions`. Run `aqv shot models/list-user`.
- **Detail.** Run `aqv click --role link --name text-embedding-ada-002`. The panel shows `EMBEDDINGS`, the served model `openai/text-embedding-ada-002`, the aliases `embedding` and `embed`, the capability `2x request limit`, and a `curl` example for `/v1/embeddings`. Under the name, `Open in Overview` and `Open the log` link to `/aqueduct/management/usage/?model=text-embedding-ada-002` and `/aqueduct/management/requests/?model=text-embedding-ada-002`. Run `aqv shot models/detail-embedding-user`.
- **Filter.** Run `aqv click --role link --name Images --exact`. The URL ends in `?type=images`, and the table lists only `dall-e-2` and `gpt-image-1`.
- **Search.** aqv cannot press Enter, so open the search by its URL: `aqv open "/aqueduct/management/models/?q=stt"`. The table lists only `whisper-1`, which matches by its alias `stt`.
- **Switch-off.** Team switch-offs are edited in the Django admin. Org switch-offs are edited on the Organization page, as [organization.md](./organization.md) shows. For a quick precondition, run `aqv shell -c "from management.models import Team; t = Team.objects.get(name='Verify Team'); t.excluded_models = ['gpt-image-1']; t.save()"`. Then run `aqv login team-member --next "/aqueduct/management/models/?model=gpt-image-1"`. The row shows `OFF FOR VERIFY TEAM`, and the panel says `Switched off for Verify Team service accounts.` and `Still available for your personal keys.` It has no admin link. Run `aqv shot models/switch-off-team-member`. As `user`, who is in no team, the same page shows no badge and no notice.
- **Admin link.** `admin` is in no team, so it sees no badge either. Run `aqv shell -c "from management.models import Team, TeamMembership, UserProfile; TeamMembership.objects.create(user_profile=UserProfile.objects.get(user__username='admin'), team=Team.objects.get(name='Verify Team'))"`, then `aqv login admin --next "/aqueduct/management/models/?model=gpt-image-1"`. The panel has the link `Edit Verify Team in admin`. Run `aqv click --role link --name "Edit Verify Team in admin"`. The title is `Verify Team (Verify Org) | Change team | Django site admin`.
- **Details.** The example config has no Hugging Face names, so write a config that has some. Run `cd aqueduct && uv run python manage.py seed_demo --write-router-config /tmp/aqv-router.yaml`, add `description`, `aliases`, and `max_tokens` to some entries, then `LITELLM_ROUTER_CONFIG_FILE_PATH=/tmp/aqv-router.yaml $AQV launch`. Open `/aqueduct/management/models/?model=QuantTrio/Qwen3.6-35B-A3B-AWQ`. The row has an `AWQ` badge. The panel has `Model card` with `href="https://huggingface.co/QuantTrio/Qwen3.6-35B-A3B-AWQ"`, `target="_blank"`, and `rel="noopener noreferrer"`, `Quantization` `AWQ`, and `Not configured` for each token limit that the entry does not set. `aqv` has no viewport or theme option. To check 390 px and dark mode, connect Playwright to the run's `cdp_port` from `state.json`, call `set_viewport_size`, and set `localStorage.selectedTheme` to `dark` before you load the page.
- **Cross-check.** Run `aqv shell -c "from gateway.config import get_router_config; print([m['model_name'] for m in get_router_config()['model_list']])"`. Every name has a row, in the same order.

## Gotchas

- A scope is where the viewer's keys live: their personal keys, and each team they are a member of. Admins and org admins see a team's switch-off only when they are members of that team.
- The example config has no description, Hugging Face name, quantization, context window, output limit, dimensions, modalities, capability flags, or deprecation date, so those cells show `Not configured` or are absent, and the Quant and Context columns, and the `DEPRECATED` badge do not appear. To see them, write a router config that has those `model_info` keys and the same six model names, and set `LITELLM_ROUTER_CONFIG_FILE_PATH` to it for `aqv launch` and for every `aqv shell` call on that run.
- The server reads the router config once. After you change the config, launch a new run.
- The copy buttons write to the clipboard, which headless Chromium does not expose. Check the copied text in the page, not the clipboard.
