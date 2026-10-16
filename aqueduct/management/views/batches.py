from typing import ClassVar

from asgiref.sync import async_to_sync
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView
from openai import OpenAIError

from gateway.config import get_files_api_client
from management.models import Batch
from management.resources import CANCELLABLE, batch_list, own
from management.views.base import BaseAqueductView


class UserBatchesView(BaseAqueductView, TemplateView):
    template_name = "management/batches.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["listing"] = batch_list(self.request.user, self.profile.teams.all(), timezone.now())
        return context


class BatchCancelView(BaseAqueductView, View):
    http_method_names: ClassVar[list] = ["post"]

    def post(self, request, *args, **kwargs) -> HttpResponse:
        batch = get_object_or_404(Batch.objects.filter(own(request.user)), pk=kwargs["id"])
        if batch.status not in CANCELLABLE:
            messages.error(request, f"Batch {batch.id} is {batch.status} and cannot be cancelled.")
            return redirect(reverse("batches"))
        try:
            async_to_sync(batch.acancel_upstream)(get_files_api_client())
        except (ValueError, OpenAIError):
            messages.error(request, f"Could not cancel batch {batch.id}. Try again later.")
        else:
            messages.success(request, f"Batch {batch.id} is being cancelled.")
        return redirect(reverse("batches"))
