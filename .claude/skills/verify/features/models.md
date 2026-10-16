# Models

The Models page lists every model in the router config with its aliases, its mode when it is not chat, a vision badge, a limit multiplier when it is not 1, and its provider settings. It shows the gateway URL with a copy button.

## Sub-features

- `models-list` shows one card per model in the router config.
- `models-aliases` shows each model's aliases.
- `models-badges` shows the mode, the vision badge, and the limit multiplier.
- `models-copy` copies the gateway URL and the model name.

## How to get to it (user POV)

- Choose **Models** in the sidebar. The path is `/aqueduct/management/models/`.

## Driving it with aqv

Preconditions:

- A fresh launch. The router config is `example_router_config.yaml` from the main checkout.

- **List.** Run `aqv login user --next /aqueduct/management/models/`. The page shows the heading `Models`, the text `Gateway URL:` with the instance's base URL, and a heading per model, starting with `gpt-4.1-nano`. Run `aqv shot models/list-user`.
- **Badges.** The `text-embedding-ada-002` card shows `2.0x Limits`, `Aliases: embedding embed`, and `Embedding`.
- **Cross-check.** Run `aqv shell -c "from gateway.config import get_router_config; print([m['model_name'] for m in get_router_config()['model_list']])"`. Every name has a card, in the same order.

## Gotchas

- Every role sees the same list. The page does not apply a user's or team's switched-off models.
- The copy buttons write to the clipboard, which headless Chromium does not expose. Check the copied text in the page, not the clipboard.
