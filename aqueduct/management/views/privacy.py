from django.views.generic import TemplateView

from management.privacy import notices_for


class PrivacyView(TemplateView):
    """Public: data protection information applies before sign-in too."""

    template_name = "management/privacy.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["notices"] = list(notices_for(self.request).select_related("org"))
        user = self.request.user
        context["can_publish"] = user.is_staff and user.has_perm("management.add_privacynotice")
        return context
