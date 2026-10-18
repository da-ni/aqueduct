# Data protection information

The Data protection information page (German: Datenschutzinformation) shows the operator's site-wide notice to everyone, signed in or not, and below it the notice for the viewer's organization. Admins see the notice of the organization chosen in the sidebar. The footer links to it as **Data protection** (German: Datenschutz) on every page, the sign-in page and the terms prompt included, whether or not a notice exists.

## Sub-features

- `privacy-empty` shows `No data protection information has been published yet.` Platform admins also see `Only platform admins see this hint.` with a link to `/aqueduct/admin/management/privacynotice/add/`.
- `privacy-site` shows the site-wide notice with `Last updated <date>` to every visitor.
- `privacy-org` shows an organization's notice under `For members of <org>` to that organization's members only.
- `privacy-link` shows **Data protection** under **Help** in the footer, on the sign-in page, and on the terms prompt, for every visitor.

## How to get to it (user POV)

- Choose **Data protection** under **Help** in the footer, including on the sign-in page.
- Open `/aqueduct/management/privacy/` directly. It loads without a sign-in, and a user who has not accepted the terms can still read it.
- Admins edit the notices under **Privacy notices** in the Django admin at `/aqueduct/admin/management/privacynotice/`.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed creates no privacy notice, so the page starts empty.

- **Empty.** Run `curl -s "$BASE/aqueduct/management/privacy/"` with `BASE` from the run state. The body contains `No data protection information has been published yet.`, the footer link `Data protection</a>`, and no `privacynotice/add` link. Sign in as `admin` and open the page. The hint `Only platform admins see this hint.` links to the add page.
- **Site-wide notice.** Run `aqv login admin`, `aqv open /aqueduct/admin/management/privacynotice/add/`, `aqv fill --label Content --value "# How we handle data"`, and `aqv click --role button --name Save --exact`. Then `curl` the page again. It shows the heading `How we handle data` and no admin hint.
- **Organization notice.** On the add page, also run `aqv select --label Org --value <Verify Org id>`, where `aqv shell -c "from management.models import Org; print(Org.objects.get(name='Verify Org').id)"` prints the id. Sign in as `user` and open the page. It shows `For members of Verify Org`. Sign in as `other-org-admin`. The page shows only the site-wide notice.
- **Admin switch.** Sign in as `admin`, choose `Other Org` in the sidebar select, and open the page. The Verify Org notice is gone.
- **Stored value.** Run `aqv shell -c "from management.models import PrivacyNotice; print(list(PrivacyNotice.objects.values_list('org__name', 'content')))"`.

## Gotchas

- Each scope takes one notice. A second site-wide notice, or a second one for the same organization, fails with `This scope already has a privacy notice. Edit that one.`
- Raw HTML in the content shows as text, and images show as links. That is intended.
