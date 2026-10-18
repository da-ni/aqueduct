---
title: Coding Standards
nav_order: 6
---

# Coding Standards

Read this page before you write or review code in `aqueduct/`, and check finished work against it. `AGENTS.md` in the repository root covers commands, tests, and the mock API. This page covers the rules the code itself has to follow.

## Enforce permissions in the view

A template that hides a button protects nothing. The view behind each URL decides who may read or change what, whatever the page shows.

- Every management view inherits `BaseAqueductView` first, which brings `LoginRequiredMixin`, and runs no permission code in `dispatch()`. Code that runs before the login check gives a signed-out request a 500 instead of the login page.
- `self.viewer` (`management/access.py`) answers every access question. Fetch every object named by an id in the URL through the viewer's queryset for the act the view performs: `viewer.keys(KeyAct.OWN)` in `TokenEditView.get_queryset()`, `team_act = TeamAct.MANAGE` on a `BaseTeamView`, `viewer.orgs()` for an org. An object outside that scope returns 404, the same as one that does not exist, and the response names no object. `management/tests/test_token_edit.py` tests the pattern.
- A refused permission is always a 404, never a flash message and a redirect. Flash messages are for business refusals, such as a member who still owns service accounts and cannot leave the team.

## Show a control only when its view allows the action

A link, button, or menu item appears exactly when the view it points to would allow the action for this user. Compute the flag in the view from the viewer act the target view uses, and pass it in the template context, as `TeamDetailView` does with `is_team_admin = viewer.may_team(TeamAct.MANAGE, team)`. Links into the Django admin depend on `user.is_staff`, because that is the flag the admin checks.

## Answer bad input with form errors

Invalid input re-renders the form with a message and status 200. It never raises. `add_error()` must name a field that exists on that form, or `None` for an error that belongs to no field. A wrong field name raises `ValueError`, and the user sees a 500.

## Test the permission boundary

Every new or changed view gets tests for who may use it and who may not. Cover a signed-out request, the allowed role, and a user outside the scope: another user, another team, or another org. Assert the status code, and assert that the database did not change. `management/tests/test_token_edit.py` shows the shape.

## Build pages from the shared templates

The management UI is server-rendered Django templates with no frontend build step. The redesign keeps that.

- Pages extend `management/base.html` and fill its `breadcrumbs`, `heading`, `subheading`, `actions` and `body` blocks; a page with a layout of its own replaces `content`. The navigation is data in `management/nav.py`. Add a page to the sidebar there, with `access` set to the check its view uses. `_sidebar.html` renders it on every page, and `_header.html` renders it as the top bar of Django admin and Silk. Templates read the `shell` context variable. They do not check roles, read feature flags, or reverse URLs that only exist behind a flag. `management/tests/test_shell.py` opens every nav page whose route is mounted as a signed-out visitor, a user, an org admin, and an admin. It fails when the shell lists a page that does not return 200 for that role, leaves out a page that does, or lists a page whose route is not mounted.
- A fragment used on more than one page is a partial named `_<name>.html` next to the pages, like `_new_key.html`. A template other pages extend has no underscore, like `resource_list.html`. Create and edit forms live in `create/` and `edit/` and extend `form_page.html`. Delete confirmations use `common/confirm_delete.html`. To ask before a destructive action on a page, put `{% confirm_delete obj %}` on a link to a delete view, or `{% confirm title detail action %}` on any other link or submit button, from `{% load ui %}`. `aqueduct.js` then asks in the shared confirm dialog.
- `management/_cdn.html` loads every third-party asset with a pinned version, then the theme in `css/aqueduct-theme.css` and the shared script in `js/aqueduct.js`. Add or upgrade an asset there and nowhere else.
- Build pages from daisyUI components, and use Tailwind utilities for layout. Take colors from the daisyUI theme, such as `bg-base-200`, `text-base-content`, and `btn-primary`, so both light and dark mode work. The templates contain no hex colors.
- New icons are inline SVGs that stroke `currentColor`, as in the redesign mockup, so they follow the theme. Add each one to the `Icon` enum in `management/icons.py` and render it with `{% icon "name" %}` from `{% load ui %}`.

## Check UI changes in a running instance

Before you open a pull request that changes a page, load the page in a running instance as each role it treats differently: signed out, user, team admin, org admin, and admin. Add screenshots of visual changes to the pull request. When the repository has a verification skill in `.claude/skills/verify/`, use it for this check.

## Write translatable strings

The management pages come in English and German, in the formal "Sie". The language follows the browser until the user picks one on the Settings page; the choice is saved in `UserProfile.language` and copied into the `django_language` cookie when they sign in. Gateway API responses stay English.

- Wrap every string a reader sees in `{% translate %}` or `{% blocktranslate %}` in templates, and in `gettext` in Python. Use `gettext_lazy` for strings in module constants, class attributes, and model or form fields, which Python evaluates before a request picks the language.
- Translate whole sentences. Put values in through named placeholders, as in `{% blocktranslate with name=team.name %}Delete {{ name }}?{% endblocktranslate %}` or `gettext("Delete %(name)s?") % {"name": name}`. Never build a sentence by joining fragments, since German orders the words differently.
- Use `{% blocktranslate count n=keys %}` or `ngettext` for every string with a number in it, never an `if` on the number.
- Give each time span its own phrase, such as "in the last 7 days" and "today", instead of one sentence with the span put in.
- Format dates a reader sees with a named format from `aqueduct/formats/` (`FORMAT_MODULE_PATH`), as in `date_format(day, "DAY_MONTH_FORMAT")` or `{{ day|date:"DAY_FORMAT" }}`, never with `strftime`, whose month and weekday names stay English. Define a new name in the `formats.py` of every language; `management/tests/test_number_formats.py` fails otherwise.
- Avoid nouns for people, such as user or owner, where the sentence works without one, since German has to give them a gender. "Created by ana@example.com" translates without one, "Owner: ana@example.com" does not.
- Language names are written in their own language, "Deutsch" and "English", and never translated.
- Render a number inside a `style` attribute through `|unlocalize` from `{% load l10n %}`. German writes 12.5 as 12,5, which is no CSS number.
- Script text comes from data attributes that the template renders, as the copy button's `data-copied` does. `aqueduct.js` holds no visible text.

`management/tests/test_i18n.py` enforces these; its catalog checks need GNU gettext on PATH and skip without it. After changing a translatable string, run `uv run python manage.py makemessages -l de --no-obsolete --no-location` from `aqueduct/`, translate every new entry in `locale/de/LC_MESSAGES/django.po`, run `uv run python manage.py compilemessages -l de`, and commit both files. Every entry needs its German text. Only the admin templates in `ENGLISH_ONLY_TEMPLATES` keep bare English text.

Use the German terms in `aqueduct/locale/de/GLOSSARY.md`.
