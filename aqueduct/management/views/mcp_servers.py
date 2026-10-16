import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.urls import reverse
from django.views.generic import TemplateView

from gateway.config import MCPServerConfig, get_mcp_config
from management.model_catalog import Availability, Scope, scopes_for, split_scopes
from management.models import Token, UserProfile
from management.views.base import BaseAqueductView


@dataclass(frozen=True)
class MCPServerCard(Availability):
    name: str
    description: str
    tags: tuple[str, ...]
    icon_url: str | None
    url: str

    @property
    def initial(self) -> str:
        return self.name[:1].upper()


def server_cards(
    config: dict[str, MCPServerConfig], base_url: str, scopes: Sequence[Scope]
) -> tuple[MCPServerCard, ...]:
    def card(name: str, server: MCPServerConfig) -> MCPServerCard:
        off_for, on_for = split_scopes(name, scopes)
        path = reverse("gateway:mcp_server", kwargs={"name": name})
        return MCPServerCard(
            off_for=off_for,
            on_for=on_for,
            name=name,
            description=server.get("description") or "",
            tags=tuple(server.get("tags") or ()),
            icon_url=server.get("icon_url"),
            url=f"{base_url}{path}",
        )

    return tuple(card(name, server) for name, server in config.items())


def client_config(card: MCPServerCard) -> str:
    entry = {"url": card.url, "headers": {"Authorization": "Bearer sk-..."}}
    return json.dumps({"mcpServers": {card.name: entry}}, indent=2)


class MCPServersView(BaseAqueductView, TemplateView):
    template_name = "management/mcp_servers.html"

    def scopes(self) -> tuple[Scope, ...]:
        try:
            return scopes_for(self.profile, Token._mcp_server_exclusion_list_from_objects)
        except UserProfile.DoesNotExist:
            return ()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        try:
            config = get_mcp_config()
        except RuntimeError as e:
            context.update(servers=(), error=str(e))
            return context
        base_url = f"{self.request.scheme}://{self.request.get_host()}"
        servers = server_cards(config, base_url, self.scopes())
        context.update(
            servers=servers, client_config=client_config(servers[0]) if servers else None
        )
        return context
