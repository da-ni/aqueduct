---
title: Overview
parent: User Guide
nav_order: 1
---

# Overview
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

The Overview page shows the token and request usage of the keys you can see. Choose **Overview** in the sidebar.

![Overview page](../assets/user_guide/overview.png)

## Whose usage you see

- Users see their own keys and the service accounts of their teams.
- Org admins see every key of their organization. **Everyone** and **My keys** switch between the whole organization and your own keys.
- Admins also choose an organization, or **All organizations**, in the organization select.

## Choose the time frame and the model

Choose **1 hour**, **24 hours**, **7 days**, or **30 days**. The page opens on **7 days**. **Custom range** opens a form for a start and an end time in UTC.

To show one model, choose it in **All models**.

The gateway keeps requests for a limited time, 30 days by default. If the time frame reaches back further, the page warns that older requests are missing.

## Read the page

- **Totals**: input tokens, output tokens, cache hits, requests, the share of failed requests, and the median response time. When the previous period of the same length is still kept, each total shows its change against that period.
- **Tokens per minute, hour, or day**: a chart of the time frame. **Tokens** and **Requests** switch what it counts.
- **Requests**: the requests split into answered (`2xx`), rate-limited (`429`), other client errors (`4xx`), and model or provider failures (`5xx`). **Open the log** opens the [Requests](requests.md) page.
- **Usage by weekday and hour**: the average tokens per hour for each weekday and hour, over the last 28 days, whatever the time frame.
- **By model**: each model with requests in the time frame, with its tokens, cache hits, requests, median response time, and share.
- **Largest keys**: the keys that used the most tokens. A key links to its page for its owner. A service account links to its team page for members of that team.
- **By organization**: the tokens of each organization, when an admin views all organizations.

## See usage per client user

If a service sends requests for many people with one key, it can send the [`user_id`](../api/index.md#sending-user_id-in-api-requests) field with each request. To see the usage per `user_id` value:

1. Open the key's page from **API keys**.
2. Choose **Open in Overview**.

The page then shows only this key, and **By user ID** replaces **Largest keys**. **Show all keys** returns to all keys.
