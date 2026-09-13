from __future__ import annotations

from typing import Any

from ehn_basestation.core.contracts import GatewayAdapter


class GatewayRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, GatewayAdapter] = {}

    def register(self, provider: GatewayAdapter) -> None:
        self._providers[provider.gateway_id] = provider

    def get_gateway(self, gateway_id: str) -> GatewayAdapter | None:
        return self._providers.get(str(gateway_id or "").strip())

    def list_providers(self) -> list[GatewayAdapter]:
        return list(self._providers.values())

    def list_gateways(self) -> list[dict[str, Any]]:
        gateways = [provider.get_status() for provider in self._providers.values()]
        gateways.sort(key=lambda item: str(item.get("gateway_id") or ""))
        return gateways

    def find_provider_for_network(self, network: str) -> GatewayAdapter | None:
        normalized = str(network or "").strip().lower()
        if not normalized:
            return None
        for provider in self._providers.values():
            if normalized in provider.supported_networks:
                return provider
        return None
