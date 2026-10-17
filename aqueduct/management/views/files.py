from asgiref.sync import async_to_sync
from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import DeleteView, TemplateView

from gateway.config import get_files_api_client
from management.deletion import deletion
from management.models import FileObject
from management.resources import file_list, list_url, own, scope
from management.views.base import BaseAqueductView


class UserFilesView(BaseAqueductView, TemplateView):
    template_name = "management/files.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["listing"] = file_list(
            self.request.user,
            scope(self.profile, self.viewed_org),
            timezone.now(),
            page=self.request.GET.get("page"),
            file_id=self.request.GET.get("file"),
        )
        return context


class FileDeleteView(BaseAqueductView, DeleteView):
    pk_url_kwarg = "id"
    template_name = "management/common/confirm_delete.html"

    def get_success_url(self) -> str:
        return list_url("files", self.request.GET.get("page"))

    def get_queryset(self) -> QuerySet[FileObject]:
        return FileObject.objects.filter(own(self.request.user))

    def form_valid(self, form) -> HttpResponse:
        file = self.object
        try:
            client = get_files_api_client()
        except ValueError:
            messages.error(self.request, "The Files API is not configured.")
            return redirect(self.get_success_url())
        if not async_to_sync(file.adelete_upstream)(client, raise_on_error=False):
            messages.error(self.request, f"Could not delete {file.filename}. Try again later.")
            return redirect(self.get_success_url())
        file.delete()
        messages.success(self.request, f"File {file.filename} deleted.")
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs) -> dict[str, object]:
        return super().get_context_data(
            confirmation=deletion(self.object), cancel_url=self.get_success_url(), **kwargs
        )
