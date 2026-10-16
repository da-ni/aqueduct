from asgiref.sync import async_to_sync
from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import DeleteView, TemplateView

from gateway.config import get_files_api_client
from management.models import FileObject
from management.resources import file_list, own
from management.views.base import BaseAqueductView


class UserFilesView(BaseAqueductView, TemplateView):
    template_name = "management/files.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["listing"] = file_list(self.request.user, self.profile.teams.all(), timezone.now())
        return context


class FileDeleteView(BaseAqueductView, DeleteView):
    pk_url_kwarg = "id"
    template_name = "management/common/confirm_delete.html"
    success_url = reverse_lazy("files")

    def get_queryset(self) -> QuerySet[FileObject]:
        return FileObject.objects.filter(own(self.request.user))

    def form_valid(self, form) -> HttpResponse:
        file = self.object
        try:
            client = get_files_api_client()
        except ValueError:
            messages.error(self.request, "The Files API is not configured.")
            return redirect(self.success_url)
        if not async_to_sync(file.adelete_upstream)(client, raise_on_error=False):
            messages.error(self.request, f"Could not delete '{file.filename}'. Try again later.")
            return redirect(self.success_url)
        file.delete()
        messages.success(self.request, f"File '{file.filename}' deleted.")
        return redirect(self.success_url)

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context.update(
            object_type_name="file", object_name=self.object.filename, cancel_url=self.success_url
        )
        if self.object.batches.exists():
            context["warning"] = "Batches that use this file as input are removed with it."
        return context
