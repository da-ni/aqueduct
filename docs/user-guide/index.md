---
title: User Guide
nav_order: 3
---

# User Guide

The Aqueduct web UI lets you create API keys, see which models and MCP servers you can use, and follow the usage of your keys and teams. Signing in opens the **API keys** page.

## Find your way around

The sidebar on the left holds every page:

- [Overview](usage.md) shows the token and request usage of the keys you can see.
- [Models](models.md) lists the models of the gateway and where each one is available to you.
- [MCP servers](mcp-servers.md) lists the MCP servers and their URLs.
- [API keys](tokens.md) lists your keys. Create, rotate, edit, and delete personal keys here.
- [Requests](requests.md) is the log of single API requests.
- [Teams](teams.md) lists your teams, their members, and their service accounts.
- Under **Resources**, [Files](files.md), [Batches](batches.md), and [Vector stores](vector-stores.md) list what your keys created through the API.
- Under **Admin**, org admins and admins get the [Organization](organization.md) page. Admins also get the **Admin panel**. See [Admin](admin.md).
- At the bottom, [Settings](settings.md) shows your account, your teams, and the limits of your personal keys.

Your deployment can add more links above **Settings**, such as a bug report form or this documentation. These links open in a new tab.

The bottom of the sidebar shows the e-mail address and role you signed in with, a light and dark switch, and the **Sign out** button.

The footer of every page shows the gateway URL with a copy button. Use this URL as the base URL in OpenAI-compatible clients. The footer also links to the terms of service, when your deployment uses them.

## Roles

What you see depends on your role:

- A **user** sees their own keys and the service accounts of their teams.
- A **team admin** also manages the members and the service accounts of their team.
- An **org admin** sees every key and team in their organization, creates and deletes teams, and edits the organization's limits and switched-off models.
- An **admin** also chooses any organization, or all of them, and opens the Admin panel.
