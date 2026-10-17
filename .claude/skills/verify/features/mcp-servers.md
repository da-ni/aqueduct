# MCP servers

The MCP servers page shows one card per server in the MCP config, with its initial or icon, name, description, and its URL on this instance in a copy field. Tags in the config are not shown. A server that none of the viewer's scopes may use is not shown. When only some may, the card says `Available with <scopes>.`, as on the Models page. A `Connect a client` card shows a client configuration with every server the viewer may use, with a copy button, and the text `Paste into your MCP client's config and replace sk-... with your API key.`, where `API key` links to the API keys page.

## Sub-features

- `mcp-list` shows one card per server, in config order.
- `mcp-copy` copies a server URL, `<base>/mcp-servers/<name>/mcp`, or the client configuration.
- `mcp-availability` hides a server no scope of the viewer may use and names the scopes when only some may.
- `mcp-error` shows `Error loading MCP servers` when the config file cannot be read.

## How to get to it (user POV)

- Choose **MCP servers** in the sidebar. The path is `/aqueduct/management/mcp-servers/`.

## Driving it with aqv

Preconditions:

- A fresh launch. The MCP config is `test-mcp.json` from the main checkout, with the servers `test-server` and `aqueduct`.

- **List.** Run `aqv login user --next /aqueduct/management/mcp-servers/`. The cards show `test-server` and `aqueduct`, with no tags. Each copy field reads `<base>/mcp-servers/<name>/mcp`. The client configuration lists both servers under `mcpServers`. Run `aqv shot mcp-servers/list-user`.
- **Availability.** Run `aqv shell -c "from management.models import Team; t = Team.objects.get(name='Verify Team'); t.excluded_mcp_servers = ['aqueduct']; t.save()"`, then `aqv login team-member --next /aqueduct/management/mcp-servers/`. The `aqueduct` card says `Available with your personal keys.` Exclude `aqueduct` on the `team-member` profile too, and the card and its entry in the client configuration are gone.

## Gotchas

- The server reads the MCP config once and caches it. Launch a new run after you change the config.
- The configuration snippet holds `Bearer sk-...` as a placeholder, never a real key.
