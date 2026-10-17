# Models

The Models page lists the router config's models that at least one of the viewer's keys may use, in a table. Each row shows the name, and under it the type and the `description` from `model_info`. A quantization badge sits next to the name at every width. The table adds an Aliases column (from 1280 px) and a Context column, each only when some model has that value. Below 1280 px, the aliases move under the name. Type tabs and a search over names and aliases filter the table. The detail shows the selected model: its type, its name, its description, the links `Model card`, `Usage`, and `Requests`, a spec cell for each spec that has a value, the served model (org admins and admins only), its aliases when it has any, its capabilities, and a `curl` example for its endpoint that sends `$OPENAI_API_KEY`. At 1280 px and wider the detail is a sticky panel beside the table. Below 1280 px it opens inline in the table, in the row `#model-detail` directly under the selected row, which is where the row links point. A `?model=` link to a model that the current tab or search filters out shows it in the panel at every width. A model that none of the viewer's scopes, their personal keys or the service accounts of a team they belong to, may use is not listed and cannot be selected. When only some scopes may use it, the panel says `Available with <scopes>.`

## Sub-features

- `models-list` shows one row per model, in router config order, with its type.
- `models-filter` filters the rows with the type tabs. A tab appears only for a type that some model has, and the tabs appear only when there are two types or more.
- `models-search` matches the search text against model names and aliases. The field submits on Enter.
- `models-detail` shows the selected model. A row link or `?model=<name or alias>` selects it, and the first listed model is selected otherwise.
- `models-availability` hides a model no scope of the viewer may use, and names the scopes that may use it when some may not. Admins get no extra view; they manage availability on the Organization page and in the Django admin.
- `models-details` shows the description, quantization, and token limits from `model_info`. A chat model shows `Quantization`, `Context length`, and `Max output tokens`, and an embedding model `Quantization` and `Max input tokens`, each only when the config or the repo name gives a value. The `Model card` link opens `model_card_url`, or `https://huggingface.co/<org>/<name>` when the served model without its provider prefix, or else the model name, is a Hugging Face repo ID. Routes to providers other than `openai/`, `hosted_vllm/`, `vllm/`, `huggingface/`, and `openai_like/` get no link. Without either, there is no link. The quantization falls back to the suffix of the repo name, such as `AWQ` or `FP8`.
- `models-usage` links the selected model to the Overview and the request log, both filtered by `?model=<name>`.
- `models-copy` copies the `curl` example. The model name and the aliases have no copy buttons, and the gateway URL is in the footer.

## How to get to it (user POV)

- Choose **Models** in the sidebar. The path is `/aqueduct/management/models/`.
- The URL holds the page state. `?type=images`, `?q=stt`, and `?model=tts` open a tab, a search, and a selected model.

## Driving it with aqv

Preconditions:

- A fresh launch. The router config is `example_router_config.yaml` from the main checkout. It gives every model aliases and a mode and gives `text-embedding-ada-002` a request limit multiplier of 2. It has no context windows, so the table has no Context column.

- **List.** Run `aqv login user --next /aqueduct/management/models/`. The table lists `gpt-4.1-nano`, `text-embedding-ada-002`, `gpt-4o-mini-tts`, `whisper-1`, `dall-e-2`, and `gpt-image-1`. The panel shows `gpt-4.1-nano` with a `curl` example for `<base>/v1/chat/completions`. Run `aqv shot models/list-user`.
- **Detail.** Run `aqv click --role link --name text-embedding-ada-002`. The panel shows `EMBEDDINGS`, the aliases `embedding` and `embed`, the capability `2x request limit`, and a `curl` example for `/v1/embeddings`. There is no `Served model` for `user`. Under the name, `Usage` and `Requests` link to `/aqueduct/management/usage/?model=text-embedding-ada-002` and `/aqueduct/management/requests/?model=text-embedding-ada-002`. Run `aqv shot models/detail-embedding-user`. As `org-admin`, the panel also shows `Served model` `openai/text-embedding-ada-002`.
- **Filter.** Run `aqv click --role link --name Images --exact`. The URL ends in `?type=images`, and the table lists only `dall-e-2` and `gpt-image-1`.
- **Search.** aqv cannot press Enter, so open the search by its URL: `aqv open "/aqueduct/management/models/?q=stt"`. The table lists only `whisper-1`, which matches by its alias `stt`.
- **Availability.** Team availability is edited in the Django admin, org availability on the Organization page, as [organization.md](./organization.md) shows. For a quick precondition, run `aqv shell -c "from management.models import Team, UserProfile; t = Team.objects.get(name='Verify Team'); t.excluded_models = ['gpt-image-1', 'whisper-1']; t.save(); p = UserProfile.objects.get(user__username='team-member'); p.excluded_models = ['whisper-1']; p.save()"`. Then run `aqv login team-member --next "/aqueduct/management/models/?model=gpt-image-1"`. The table has no `whisper-1` row, and the panel says `Available with your personal keys.` Run `aqv shot models/availability-team-member`. As `user`, who is in no team, `gpt-image-1` shows no `Available with` line and `whisper-1` is listed.
- **Details.** The example config has no Hugging Face names, so write a config that has some. Run `cd aqueduct && uv run python manage.py seed_demo --write-router-config /tmp/aqv-router.yaml`, add `description`, `aliases`, and `max_tokens` to some entries, then `LITELLM_ROUTER_CONFIG_FILE_PATH=/tmp/aqv-router.yaml $AQV launch`. Open `/aqueduct/management/models/?model=QuantTrio/Qwen3.6-35B-A3B-AWQ`. The row has an `AWQ` badge. The panel has `Model card` with `href="https://huggingface.co/QuantTrio/Qwen3.6-35B-A3B-AWQ"`, `target="_blank"`, and `rel="noopener noreferrer"`, `Quantization` `AWQ`, and no cell for a token limit that the entry does not set. `aqv` has no viewport or theme option. To check 390 px, where the detail opens inline under the selected row, and dark mode, connect Playwright to the run's `cdp_port` from `state.json`, call `set_viewport_size`, and set `localStorage.selectedTheme` to `dark` before you load the page.
- **Cross-check.** Run `aqv shell -c "from gateway.config import get_router_config; print([m['model_name'] for m in get_router_config()['model_list']])"`. Every name has a row, in the same order.

## Gotchas

- A scope is where the viewer's keys live: their personal keys, and each team they are a member of. Admins and org admins see only what their own scopes may use. A `?model=` link to a hidden model selects the first listed model instead.
- The example config has no description, Hugging Face name, quantization, context window, output limit, dimensions, modalities, capability flags, or deprecation date, so those cells, the quantization badge, the Context column, and the `DEPRECATED` badge do not appear. To see them, write a router config that has those `model_info` keys and the same six model names, and set `LITELLM_ROUTER_CONFIG_FILE_PATH` to it for `aqv launch` and for every `aqv shell` call on that run.
- The server reads the router config once. After you change the config, launch a new run.
- The copy button writes to the clipboard, which headless Chromium does not expose. Check the copied text in the page, not the clipboard.
