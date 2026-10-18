from typing import Any

from django.views.generic import TemplateView

from management.model_catalog import Category, Scope, load_catalog, scopes_for
from management.models import UserProfile
from management.nav import gateway_url
from management.views.base import BaseAqueductView


class ModelListView(BaseAqueductView, TemplateView):
    template_name = "management/model_list.html"

    def scopes(self) -> tuple[Scope, ...]:
        try:
            return scopes_for(self.profile)
        except UserProfile.DoesNotExist:
            return ()

    def shows_served_model(self) -> bool:
        try:
            return self.profile.is_org_admin(self.profile.org)
        except UserProfile.DoesNotExist:
            return False

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        catalog = load_catalog(self.scopes())
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
            show_served_model=self.shows_served_model(),
        )
        return context
