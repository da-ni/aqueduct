import logging

from asgiref.sync import async_to_sync
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from management.models import VectorStore
from management.resources import store_detail, store_list, stores_visible_to
from management.views.base import BaseAqueductView

log = logging.getLogger(__name__)


class UserVectorStoresView(BaseAqueductView, TemplateView):
    template_name = "management/vector_stores.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["listing"] = store_list(
            self.request.user, self.get_teams_for_user(), timezone.now()
        )
        return context


class VectorStoreDetailView(BaseAqueductView, TemplateView):
    template_name = "management/vector_store_detail.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        store = (
            stores_visible_to(self.request.user, self.get_teams_for_user())
            .filter(id=self.kwargs["id"])
            .first()
        )
        context["detail"] = store_detail(store, timezone.now()) if store else None
        return context


def _refresh_vector_store(vs) -> bool:
    """
    Refresh a single VectorStore from upstream.
    Calls areload_from_upstream() with raise_on_error=False.
    Returns True if the refresh succeeded, False otherwise.
    """
    try:
        result = async_to_sync(vs.areload_from_upstream)(raise_on_error=False)
    except Exception:
        log.exception("Failed to refresh vector store %s from upstream", vs.id)
        return False
    else:
        return result is not None


class VectorStoreCardRefreshView(BaseAqueductView, View):
    """
    POST-only view that refreshes a single vector store from the upstream API,
    then redirects back to the vector stores list page.
    """

    def post(self, request, *args, **kwargs) -> HttpResponse:
        user = self.profile.user
        teams = self.get_teams_for_user()
        vector_store_id = self.kwargs.get("id")

        try:
            vs = VectorStore.objects.get(
                Q(id=vector_store_id)
                & (Q(token__user=user) | Q(token__service_account__team__in=teams))
            )
        except VectorStore.DoesNotExist:
            messages.error(request, "Vector store not found or you do not have access.")
            return redirect(reverse("vector_stores"))

        if _refresh_vector_store(vs):
            messages.success(request, f'Synced "{vs.name}" from upstream.')
        else:
            messages.warning(request, f'Failed to sync "{vs.name}" from upstream.')

        return redirect(reverse("vector_stores"))


class VectorStoreDetailRefreshView(BaseAqueductView):
    """
    POST-only view that refreshes a specific vector store and all its files
    from the upstream API, then redirects back to the detail page.
    """

    def post(self, request, *args, **kwargs) -> HttpResponse:
        user = self.profile.user
        teams = self.get_teams_for_user()
        vector_store_id = self.kwargs.get("id")

        try:
            vs = VectorStore.objects.get(
                Q(id=vector_store_id)
                & (Q(token__user=user) | Q(token__service_account__team__in=teams))
            )
        except VectorStore.DoesNotExist:
            messages.error(request, "Vector store not found or you do not have access.")
            return redirect(reverse("vector_stores"))

        if _refresh_vector_store(vs):
            messages.success(request, f'Synced "{vs.name}" from upstream.')
        else:
            messages.warning(request, f'Failed to sync "{vs.name}" from upstream.')

        return redirect(reverse("vector_store_detail", kwargs={"id": vector_store_id}))
