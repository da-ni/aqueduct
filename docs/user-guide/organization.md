---
title: Organization
parent: User Guide
nav_order: 10
---

# Organization
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

The Organization page shows the default limits of your organization and what is unavailable in it. Org admins and admins edit both here. Choose **Organization** under **Admin** in the sidebar.

Only org admins of the organization and admins see this page. Other users who open it are sent to the [Teams](teams.md) page.

![Organization page](../assets/user_guide/organization.png)

## Read the page

The top of the page shows the number of people, teams, and service accounts in the organization. The team count opens the Teams page.

- **Limits** shows the default limits for each key in the organization. Teams and people use these limits unless they have limits of their own. **Personal keys per person** comes from the server configuration and cannot be changed here.
- **Unavailable** shows the models and MCP servers that are unavailable to everyone in the organization, and to each team that has its own list. A team name opens its team page.

Admins see the organization they chose in the sidebar. See [Choose an organization](index.md#choose-an-organization).

## Change the limits

1. Choose **Edit** on the **Limits** card.
2. Set the limits:
    - **Requests per minute, per key**, **Input tokens per minute, per key**, and **Output tokens per minute, per key**. Leave a field empty for no limit.
    - **Hourly limit, in per-minute limits**, from 1 to 60, and **Daily limit, in per-minute limits**, from 1 to 1440. For example, an hourly value of 30 allows 30 times the per-minute limit in one hour. Leave a field empty for the server default.
3. Choose **Save changes**.

The **Edit** link next to the default limit on the Teams page opens the same form.

If a request goes over a limit, the API answers with HTTP status `429 Too Many Requests`.

## Choose the available models and MCP servers

1. Choose **Edit** on the **Unavailable** card.
2. Under **Available models** and **Available MCP servers**, clear the box of each one that keys in the organization must not use. Every box starts checked. **Select all** and **Select none** set a long list at once.
3. Choose **Save changes**.

A request to an unavailable model or MCP server returns `404`. The [Models](models.md) and [MCP servers](mcp-servers.md) pages do not show it to people who cannot use it. A model added to the configuration later is available until you clear its box.

The server configuration can also make models or MCP servers unavailable everywhere. The form then shows **Apply the server's default unavailable models** or **Apply the server's default unavailable MCP servers**. Clear the box to make them available in this organization.

The form lists the models and MCP servers of the current configuration. An unavailable name that the configuration no longer has shows as a cleared box labelled `(not in the current config)`. Saving keeps it unavailable. Check the box to remove it.

Team and user availability is edited in the Admin panel. See [Model availability](admin.md#model-availability).
