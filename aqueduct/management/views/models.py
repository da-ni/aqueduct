from typing import Any

from django.views.generic import TemplateView

from management.model_catalog import Category, load_catalog
from management.nav import gateway_url
from management.views.base import BaseAqueductView


class ModelListView(BaseAqueductView, TemplateView):
    template_name = "management/model_list.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        # Only the sign-in flow creates profiles, so a user made with createsuperuser has none.
        catalog = load_catalog(getattr(self.request.user, "profile", None))
        category = Category.parse(params.get("type", ""))
        query = params.get("q", "").strip()
        cards = catalog.search(category, query)
        selected = catalog.find(params.get("model", "")) or next(iter(cards or catalog.cards), None)
        context.update(
            catalog=catalog,
            category=category,
            query=query,
            cards=cards,
            selected=selected,
            snippet=selected.curl(gateway_url(self.request)) if selected else None,
        )
        return context
