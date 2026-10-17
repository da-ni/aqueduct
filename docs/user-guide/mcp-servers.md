---
title: MCP servers
parent: User Guide
nav_order: 3
---

# MCP servers

The MCP servers page lists the Model Context Protocol (MCP) servers that Aqueduct relays. An MCP server gives an AI client tools, such as search or database access. Choose **MCP servers** in the sidebar.

![MCP servers page](../assets/user_guide/mcp_servers.png)

## Read a server card

Each card shows:

- The server name. Use this exact name in the server URL.
- A description.
- The server URL on this gateway, with a copy button.

The URL has this form:

```
https://your-aqueduct-domain.com/mcp-servers/<server-name>/mcp
```

The page lists only the servers that at least one of your keys can use. When only some of your keys can use a server, its card names them, as on the [Models](models.md#check-which-keys-can-use-a-model) page. A request with another key returns `404`.

## Connect a client

1. Copy the server URL from its card.
2. Configure your MCP client with the URL, and send your API key as a bearer token in the `Authorization` header.

The **Connect a client** card shows a client configuration with every server you can use, with a copy button. Paste it into your MCP client's configuration and replace `sk-...` with your API key.

For the API details, see the [MCP API reference](../api/mcp.md).
