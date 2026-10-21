from __future__ import annotations

from typing import Any

from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.gateways.meshcore.gateway_link import (
    HttpMeshCoreGatewayLink,
    MeshCoreGatewayLink,
    MeshCoreRuntimeSettings,
    StreamMeshCoreGatewayLink,
)


class MeshCoreRuntimeClient:
    def __init__(self, settings_or_link: MeshCoreRuntimeSettings | MeshCoreGatewayLink) -> None:
        if isinstance(settings_or_link, MeshCoreRuntimeSettings):
            self._link: MeshCoreGatewayLink = HttpMeshCoreGatewayLink(settings_or_link)
        else:
            self._link = settings_or_link

    @classmethod
    def from_settings(cls, settings: BasestationSettings) -> MeshCoreRuntimeClient:
        runtime_settings = MeshCoreRuntimeSettings(
            gateway_id=settings.meshcore_gateway_id,
            display_name=settings.meshcore_display_name,
            base_url=settings.meshcore_base_url,
            contract_version=settings.meshcore_contract_version,
            enabled=settings.meshcore_enabled,
            auth_token=settings.meshcore_auth_token,
            timeout_seconds=settings.meshcore_timeout_seconds,
        )
        carrier = str(settings.meshcore_link_carrier or "http").strip().lower() or "http"
        if carrier == "http":
            return cls(HttpMeshCoreGatewayLink(runtime_settings))

        endpoint = str(settings.meshcore_link_endpoint or "").strip()
        return cls(StreamMeshCoreGatewayLink(runtime_settings, carrier=carrier, endpoint=endpoint))

    @property
    def gateway_id(self) -> str:
        return self._link.gateway_id

    def get_snapshot(self) -> dict[str, Any]:
        return self._link.get_snapshot()

    def list_channels(self) -> list[dict[str, Any]]:
        return self._link.list_channels()

    def list_observed_nodes(self) -> list[dict[str, Any]]:
        return self._link.list_observed_nodes()

    def list_gateway_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        return self._link.list_gateway_inbound_events(limit=limit, cursor=cursor)

    def list_gateway_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        return self._link.list_gateway_outbound_status(limit=limit, state=state)

    def get_device_details(self) -> dict[str, Any]:
        return self._link.get_device_details()

    def set_tx_power(self, *, tx_power_dbm: int) -> tuple[bool, str, dict[str, Any] | None]:
        return self._link.set_tx_power(tx_power_dbm=tx_power_dbm)

    def send_zero_hop_advert(self) -> tuple[bool, str, dict[str, Any] | None]:
        sender = getattr(self._link, "send_zero_hop_advert", None)
        if sender is None:
            return False, "This gateway does not support zero-hop adverts.", None
        return sender()

    def send_directed_private_message(
        self,
        *,
        recipient_public_key: str,
        text: str,
        sender_identity: dict[str, Any] | None = None,
        destination_identity: dict[str, Any] | None = None,
        delivery_id: str | None = None,
        reply_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        return self._link.send_directed_private_message(
            recipient_public_key=recipient_public_key,
            text=text,
            sender_identity=sender_identity,
            destination_identity=destination_identity,
            delivery_id=delivery_id,
            reply_context=reply_context,
        )

    def send_channel_message(
        self,
        *,
        channel_index: int,
        text: str,
        channel_name: str | None = None,
        delivery_id: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        return self._link.send_channel_message(
            channel_index=channel_index,
            text=text,
            channel_name=channel_name,
            delivery_id=delivery_id,
        )