---
title: Models
parent: User Guide
nav_order: 2
---

# Models
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

The Models page lists every model of the gateway and shows how to call it. Choose **Models** in the sidebar.

![Models page](../assets/user_guide/models.png)

## Find a model

The table lists each model with its type, such as **Chat**, **Embeddings**, **Text to speech**, **Speech to text**, or **Images**. A short description is under the model name when the admin wrote one. When the gateway knows them, the table also shows:

- **Aliases**, other names that you can send for the model. On a narrow screen, they are under the model name.
- **Quant**, the quantization of the model weights, such as `FP8` or `AWQ`.
- **Context**, the context length, such as `262.1K`. For a chat model, this is the input and output tokens of one request together. For an embedding model, it is the maximum input tokens.

- To show one type, choose its tab above the table.
- To find a model by name or alias, type in **Search models and aliases** and press Enter.

The gateway URL with a copy button is at the top right.

## Read a model's details

Choose a model to show its details in the panel on the right. The panel shows:

- The model name with a copy button. Use this name in the `model` parameter of your requests.
- The description of the model, when the admin wrote one.
- **Model card**, a link to the model's page, usually on Hugging Face. It opens in a new tab.
- **Quantization**, **Context length**, and **Max output tokens**, with full numbers. An embedding model shows **Max input tokens** instead of the context length. A value that the admin did not configure shows as **Not configured**.
- Other specs the gateway knows, such as the embedding dimensions and the input and output types.
- **Served model**, the upstream model that answers the requests.
- **Aliases**, other names for the same model, each with a copy button.
- **Capabilities**, such as vision, tools, or reasoning, and a higher request limit when the model has one.
- **Use it**, a `curl` command for the model's endpoint, with a copy button.

A model with a retirement date carries a **Deprecated** badge, and its panel shows the date. Move to another model before that date.

## Use an alias

You can send an alias instead of the model name in the `model` parameter. The model behind an alias can change, for example when a newer version replaces it. Your requests keep working without a change.

## Check where a model is available

Admins can switch a model off for an organization, a team, or a single user. The page checks this for each place your keys live: your personal keys, and the service accounts of each team you are in.

- A model switched off for some of your keys shows a badge such as `Off for Verify Team`. The panel says where the model is switched off and where you can still use it.
- A model switched off for all of your keys shows `Off for you`.

A request to a switched-off model returns `404`. To change where a model is switched off, ask an admin. Admins get an **Edit … in admin** link in the panel. See [Excluding Models](admin.md#excluding-models).
