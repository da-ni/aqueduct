from django.views.generic import TemplateView

from management.privacy import notices_for


class PrivacyView(TemplateView):
    """Public: the privacy notice applies before sign-in too."""

    template_name = "management/privacy.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["notices"] = list(notices_for(self.request).select_related("org"))
        return context
