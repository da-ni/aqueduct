---
title: Coding Standards
nav_order: 6
---

# Coding Standards

Read this page before you write or review code in `aqueduct/`, and check finished work against it. `AGENTS.md` in the repository root covers commands, tests, and the mock API. This page covers the rules the code itself has to follow.

## Enforce permissions in the view

A template that hides a button protects nothing. The view behind each URL decides who may read or change what, whatever the page shows.

- Every management view inherits `BaseAqueductView`, which brings `LoginRequiredMixin`. Put permission checks in a mixin listed after it, as `ServiceAccountCreateView` does, or in `get_queryset()`. A permission mixin listed before `BaseAqueductView`, or a `dispatch()` override in the view class, runs before the login check. A signed-out request then gets a 500 instead of the login page.
- Fetch every object named by an id in the URL through a queryset limited to what the user may act on. That is their own objects (`user=request.user`), their teams, or their org. An object outside that scope returns 404, the same as one that does not exist. `TokenEditView.get_queryset()` is the pattern, and `management/tests/test_token_edit.py` tests it.
- Check roles with the `UserProfile` helpers `is_admin()`, `is_org_admin(org)`, and `is_team_admin(team)`. Pass the org or team of the object being changed, not the user's own. A check against the user's own org lets an org admin act on another org's teams by id.

## Show a control only when its view allows the action

A link, button, or menu item appears exactly when the view it points to would allow the action for this user. Compute the flag in the view with the same helper the target view uses, and pass it in the template context, as `TeamDetailView` does with `is_team_admin`. Links into the Django admin depend on `user.is_staff`, because that is the flag the admin checks.

## Answer bad input with form errors

Invalid input re-renders the form with a message and status 200. It never raises. `add_error()` must name a field that exists on that form, or `None` for an error that belongs to no field. A wrong field name raises `ValueError`, and the user sees a 500.

## Test the permission boundary

Every new or changed view gets tests for who may use it and who may not. Cover a signed-out request, the allowed role, and a user outside the scope: another user, another team, or another org. Assert the status code, and assert that the database did not change. `management/tests/test_token_edit.py` shows the shape.

## Build pages from the shared templates

The management UI is server-rendered Django templates with no frontend build step. The redesign keeps that.

- Pages extend `management/base.html` and fill `{% block content %}`. The navigation is data in `management/nav.py`. Add a page to the sidebar there, with `access` set to the check its view uses. `_sidebar.html` renders it on every page, and `header.html` renders it as the top bar of Django admin and Silk. Templates read the `shell` context variable. They do not check roles, read feature flags, or reverse URLs that only exist behind a flag. `management/tests/test_shell.py` opens every nav page whose route is mounted as a signed-out visitor, a user, an org admin, and an admin. It fails when the shell lists a page that does not return 200 for that role, leaves out a page that does, or lists a page whose route is not mounted.
- A fragment used on more than one page is a partial named `_<name>.html` next to the pages, like `_new_key.html`. Create and edit forms live in `create/` and `edit/`. Delete confirmations use `common/confirm_delete.html`.
- `management/_cdn.html` loads every third-party asset with a pinned version, then the theme in `css/aqueduct-theme.css` and the shared script in `js/aqueduct.js`. Add or upgrade an asset there and nowhere else.
- Build pages from daisyUI components, and use Tailwind utilities for layout. Take colors from the daisyUI theme, such as `bg-base-200`, `text-base-content`, and `btn-primary`, so both light and dark mode work. The templates contain no hex colors.
- New icons are inline SVGs that stroke `currentColor`, as in the redesign mockup, so they follow the theme. Add each one to the `Icon` enum in `management/icons.py` and render it with `{% icon "name" %}` from `{% load ui %}`. The `[[data-theme=dark]_&]:invert` class exists only for the older `<img>` icons.

## Check UI changes in a running instance

Before you open a pull request that changes a page, load the page in a running instance as each role it treats differently: signed out, user, team admin, org admin, and admin. Add screenshots of visual changes to the pull request. When the repository has a verification skill in `.claude/skills/verify/`, use it for this check.

## Write translatable strings

The management pages come in English and German, in the formal "Sie". The language follows the browser until the user picks one on the Settings page; the choice is saved in `UserProfile.language` and copied into the `django_language` cookie when they sign in. Gateway API responses stay English.

- Wrap every string a reader sees in `{% translate %}` or `{% blocktranslate %}` in templates, and in `gettext` in Python. Use `gettext_lazy` for strings in module constants, class attributes, and model or form fields, which Python evaluates before a request picks the language.
- Translate whole sentences. Put values in through named placeholders, as in `{% blocktranslate with name=team.name %}Delete {{ name }}?{% endblocktranslate %}` or `gettext("Delete %(name)s?") % {"name": name}`. Never build a sentence by joining fragments, since German orders the words differently.
- Use `{% blocktranslate count n=keys %}` or `ngettext` for every string with a number in it, never an `if` on the number.
- Give each time span its own phrase, such as "in the last 7 days" and "today", instead of one sentence with the span put in.
- Format dates a reader sees with `django.utils.dateformat` or the `date` filter, as in `dateformat.format(day, "j M")`, never with `strftime`, whose month and weekday names stay English.
- Avoid nouns for people, such as user or owner, where the sentence works without one, since German has to give them a gender. "Created by ana@example.com" translates without one, "Owner: ana@example.com" does not.
- Language names are written in their own language, "Deutsch" and "English", and never translated.
- Render a number inside a `style` attribute through `|unlocalize` from `{% load l10n %}`. German writes 12.5 as 12,5, which is no CSS number.
- Script text comes from data attributes that the template renders, as the copy button's `data-copied` does. `aqueduct.js` holds no visible text.

`management/tests/test_i18n.py` enforces these. Its docstring names the commands that refresh `aqueduct/locale/de/LC_MESSAGES/django.po` and the compiled `django.mo`; commit both. Its two lists only shrink. A template leaves `UNTRANSLATED_TEMPLATES` once all its text is wrapped, and a msgid in `UNTRANSLATED` is waiting for its German text.

### Glossary

| English | German |
|---|---|
| API key | API-Schlüssel |
| Personal key | persönlicher Schlüssel |
| Service account | Dienstkonto |
| Organization | Organisation |
| Platform admin | Plattform-Admin |
| Org admin | Organisations-Admin |
| Team admin | Team-Admin |
| Member | Mitglied |
| Request | Anfrage |
| Request log | Anfrageprotokoll |
| Token, tokens | Token |
| Cached | aus dem Cache |
| Rate-limited | Limit erreicht |
| Usage | Nutzung |
| Overview | Übersicht |
| Models | Modelle |
| Context length | Kontextlänge |
| Answered, OK | OK |
| Failed | Fehlgeschlagen |
| Never | Nie |
| Files | Dateien |
| Batches | Batches |
| Vector stores | Vektorspeicher |
| MCP server | MCP-Server |
| Median response time | Median der Antwortzeit |
| Input, New input, Output | Eingabe, Neue Eingabe, Ausgabe |
| Cache hits | Cache-Treffer |
| Client error, Server error | Clientfehler, Serverfehler |
| Time frame | Zeitraum |
| Share (of a total) | Anteil |
| Top keys | Meistgenutzte Schlüssel |
| User ID (the `user` value a client sends) | User-ID |
| Approximate | Näherungswert |
| Search | Suchen |
| n/a | k. A. |
| Settings | Einstellungen |
| Sign in, Sign out | Anmelden, Abmelden |
| Terms of service | Nutzungsbedingungen |
| Privacy | Datenschutz |
| Copy, Copied | Kopieren, Kopiert |
| Delete, Cancel, Save | Löschen, Abbrechen, Speichern |
| Account | Konto |
| Role | Rolle |
| Limit | Limit |
| Organization default | Vorgabe der Organisation |
| Viewing (an organization) | Ansicht |
| Accept, Decline | Akzeptieren, Ablehnen |
| Version of (a date) | Fassung vom |
| Previous, Next | Zurück, Weiter |
| Aqueduct, Aqueduct AI Gateway | unchanged, as product names |

German compact numbers use "Tsd.", "Mio.", "Mrd.", and "Bio.".
