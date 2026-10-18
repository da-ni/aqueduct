---
title: User Guide
nav_order: 3
---

# User Guide

The Aqueduct web UI lets you create API keys, see which models and MCP servers you can use, and follow the usage of your keys and teams. Signing in opens the **Overview** page.

## Find your way around

The sidebar on the left holds every page. Admins also get the **Viewing** select at its top. See [Choose an organization](#choose-an-organization).

- [Overview](usage.md) shows the token and request usage of the keys you can see.
- [Models](models.md) lists the models of the gateway and where each one is available to you.
- [MCP servers](mcp-servers.md) lists the MCP servers and their URLs.
- [API keys](tokens.md) lists your keys. Create, rotate, edit, and delete personal keys here.
- [Requests](requests.md) is the log of single API requests.
- [Teams](teams.md) lists your teams, their members, and their service accounts.
- Under **Resources**, [Files](files.md), [Batches](batches.md), and [Vector stores](vector-stores.md) list what your keys created through the API.
- Under **Admin**, org admins and admins get the [Organization](organization.md) page. Admins also get the **Admin panel**. See [Admin](admin.md).
- At the bottom, [Settings](settings.md) shows your account, your teams, and the limits of your personal keys.

The bottom of the sidebar shows the e-mail address and role you signed in with, a light and dark switch, and the **Sign out** button.

## Choose an organization

Admins choose the organization they look at, or **All organizations**, in the **Viewing** select at the top of the sidebar. The Overview, All keys, Requests, Teams, and Organization pages then show that organization until you change it or sign out. While you look at all of them, the Teams page shows your own organization and says so, and the Organization page lists every organization so you can open one. When you change the organization, the pages drop the key you had chosen and keep the model and time frame.

A link with `?org=` in its address, such as a link someone shared, shows that organization and keeps it as your choice. Org admins and users always see their own organization and get no select.

The footer of every page shows the gateway URL with a copy button once you are signed in. Use this URL as the base URL in OpenAI-compatible clients. Under **Help**, the footer links to **Report a bug**, the documentation, the terms of service when your deployment uses them, and the **Data protection** information page. Icon links to the Matrix support channel and the GitHub repository appear below them. Your deployment can point them elsewhere with `MATRIX_SUPPORT_URL` and `GITHUB_REPOSITORY_URL`, or hide one by setting its variable to an empty value. The data protection information page shows the information for everyone, followed by any information for your organization.

## Roles

What you see depends on your role:

- A **Member** sees their own keys and the service accounts of their teams.
- A **Team admin** also manages the members and the service accounts of their team.
- An **Org admin** sees every key and team in their organization, creates and deletes teams, and edits the organization's limits and switched-off models.
- A **Platform admin** also chooses any organization, or all of them, and opens the Admin panel.

A page about a team or service account that you may not open answers **404**, the same as a page that does not exist.

## Confirm a delete

**Delete**, **Rotate**, and removing a member ask first in a dialog on the same page. The dialog names what goes with the item, such as the requests, files, batches, and vector stores of a deleted key. Choose the action button to go ahead, or **Cancel** or Esc to keep the item. Without JavaScript, or with a middle click, **Delete** opens a page that asks the same question.
