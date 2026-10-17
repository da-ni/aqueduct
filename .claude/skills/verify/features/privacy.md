# Privacy

The Privacy page shows the operator's site-wide privacy notice to everyone, signed in or not, and below it the notice for the viewer's organization. Admins see the notice of the organization chosen in the sidebar. The footer and the sign-in page link to it only while the viewer has a notice to read.

## Sub-features

- `privacy-empty` shows `No privacy information has been published yet.` and no **Privacy** link anywhere.
- `privacy-site` shows the site-wide notice with `Last updated <date>` to every visitor.
- `privacy-org` shows an organization's notice under `For members of <org>` to that organization's members only.
- `privacy-link` adds **Privacy** under **Help** in the footer, on the sign-in page, and on the terms prompt.

## How to get to it (user POV)

- Choose **Privacy** under **Help** in the footer, or the **Privacy** link below the sign-in button.
- Open `/aqueduct/management/privacy/` directly. It loads without a sign-in, and a user who has not accepted the terms can still read it.
- Admins edit the notices under **Privacy notices** in the Django admin at `/aqueduct/admin/management/privacynotice/`.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed creates no privacy notice, so the page starts empty.

- **Empty.** Run `curl -s "$BASE/aqueduct/management/privacy/"` with `BASE` from the run state. The body contains `No privacy information has been published yet.` and no `Privacy</a>` link.
- **Site-wide notice.** Run `aqv login admin`, `aqv open /aqueduct/admin/management/privacynotice/add/`, `aqv fill --label Content --value "# How we handle data"`, and `aqv click --role button --name Save --exact`. Then `curl` the page again. It shows the heading `How we handle data` and the footer link `Privacy`.
- **Organization notice.** On the add page, also run `aqv select --label Org --value <Verify Org id>`, where `aqv shell -c "from management.models import Org; print(Org.objects.get(name='Verify Org').id)"` prints the id. Sign in as `user` and open the page. It shows `For members of Verify Org`. Sign in as `other-org-admin`. The page shows only the site-wide notice.
- **Admin switch.** Sign in as `admin`, choose `Other Org` in the sidebar select, and open the page. The Verify Org notice is gone.
- **Stored value.** Run `aqv shell -c "from management.models import PrivacyNotice; print(list(PrivacyNotice.objects.values_list('org__name', 'content')))"`.

## Gotchas

- Each scope takes one notice. A second site-wide notice, or a second one for the same organization, fails with `This scope already has a privacy notice. Edit that one.`
- Raw HTML in the content shows as text, and images show as links. That is intended.
