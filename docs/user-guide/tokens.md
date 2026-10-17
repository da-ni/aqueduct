---
title: API keys
parent: User Guide
nav_order: 4
---

# API keys
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

An API key authenticates your requests to the gateway. A key works for every model that is available to you. Aqueduct passes requests through to OpenAI-compatible APIs, such as the OpenAI API or local models served by vLLM.

Choose **API keys** in the sidebar to open the list. Signing in opens the same page.

![API keys page](../assets/user_guide/api_keys.png)

## Read the key list

The table shows your personal keys and the service account keys of your teams. Each row shows:

- **Key**: the name, the type (**Personal** or **Service account** with the team name), and the last four characters of the key.
- **Last used**: when the key last made a request, or **—** if it never did.
- **Tokens, 7 days**: the tokens of the last seven days, split into input from cache, new input, and output.
- **Requests**: the requests of the last seven days.
- **Expires**: the expiry date, or **No expiry**.

If you are in a team with a service account, the tabs **All**, **Personal**, and **Service accounts** filter the table. The search box filters by key or team name.

**Create API key** shows how many personal keys you have, such as `1/3`. Each user can have three personal keys. Service accounts do not count toward this limit.

On a phone, the **⋯** button at the end of a row opens the row's actions.

## Create a key

1. Choose **Create API key**.
2. Enter a **Name**.
3. Optional: set **Expires**. Leave it empty for a key that never expires.
4. Choose **Create**.

The **Your key is ready** panel shows the full key and commands that set `OPENAI_BASE_URL` and `OPENAI_API_KEY` and list the models with `curl`. OpenAI client libraries read the same two variables.

![Your key is ready panel](../assets/user_guide/key_ready.png)

Choose **Copy key** and keep the key somewhere safe, such as a password manager. Aqueduct does not store the key and does not show it again. After this, the list shows only the last four characters.

If you already have three personal keys, the button reads **3 of 3 keys used** and is disabled. Delete a key to create another.

## Open a key

Choose a key's name to open its page. The page shows the key's last seven days:

- Totals for input tokens, output tokens, cache hits, requests, and failed requests.
- Tokens or requests per day.
- **Models**, with the tokens per model.
- **Clients**, the user agents that used the key.
- **Latest requests**. **All requests** opens the [request log](requests.md) for this key.
- **Limits**, the rate limits set for the key, or **No limits**, and how many models and MCP servers it can call.

![API key page](../assets/user_guide/key_detail.png)

Org admins can open the page of every key in their organization, and admins the page of every key. They see the page without the buttons, unless the key is theirs or belongs to a team they administer.

## Rotate, edit, or delete a key

The buttons at the end of each row, and at the top of the key's page, change a key. You can change your personal keys, and team admins can change their team's service accounts:

- **Rotate** replaces the key with a new one and shows it in the **Your key is ready** panel. The old key stops working immediately.
- **Edit** changes the name or the expiry. For a service account it opens the service account's form.
- **Delete** asks first, then deletes the key with its requests, files, batches, and vector stores. For a service account it deletes the service account. See [Confirm a delete](index.md#confirm-a-delete).

An expired key no longer authenticates requests.

Team members who are not team admins see service account rows without buttons. Team admins can also manage service accounts on the [team page](teams.md#manage-service-accounts).
