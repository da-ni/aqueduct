# MCP servers

The MCP servers page shows one card per server in the MCP config, with its initial or icon, name, description, tags, and its URL on this instance in a copy field. A server that is switched off for one of the viewer's scopes carries an `OFF FOR <scope>` badge, as on the Models page. A `Connect a client` card shows a client configuration for the first server, with a copy button.

## Sub-features

- `mcp-list` shows one card per server, in config order.
- `mcp-copy` copies a server URL, `<base>/mcp-servers/<name>/mcp`, or the client configuration.
- `mcp-switch-off` shows the badge for a server switched off for the viewer's personal keys or a team's service accounts.
- `mcp-error` shows `Error loading MCP servers` when the config file cannot be read.

## How to get to it (user POV)

- Choose **MCP servers** in the sidebar. The path is `/aqueduct/management/mcp-servers/`.

## Driving it with aqv

Preconditions:

- A fresh launch. The MCP config is `test-mcp.json` from the main checkout, with the servers `test-server` and `aqueduct`.

- **List.** Run `aqv login user --next /aqueduct/management/mcp-servers/`. The cards show `test-server` with the tags `development` and `testing`, and `aqueduct` with `production` and `api`. Each copy field reads `<base>/mcp-servers/<name>/mcp`. Run `aqv shot mcp-servers/list-user`.
- **Switch-off.** Run `aqv shell -c "from management.models import Team; t = Team.objects.get(name='Verify Team'); t.excluded_mcp_servers = ['aqueduct']; t.save()"`, then `aqv login team-member --next /aqueduct/management/mcp-servers/`. The `aqueduct` card shows `OFF FOR VERIFY TEAM`.

## Gotchas

- The server reads the MCP config once and caches it. Launch a new run after you change the config.
- The configuration snippet holds `Bearer sk-...` as a placeholder, never a real key.
