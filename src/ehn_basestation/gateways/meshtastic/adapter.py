from __future__ import annotations

from typing import Any

from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest
from ehn_basestation.core.delivery_queue import DeliveryQueueService, RouteDeliveryWorkItem
from ehn_basestation.gateways.meshtastic.broker import MeshtasticGatewayBroker


class MeshtasticGatewayAdapter:
    def __init__(
        self,
        *,
        broker: MeshtasticGatewayBroker,
        delivery_queue: DeliveryQueueService | None = None,
        station_id: str = "local-station",
    ) -> None:
        self.broker = broker
        self.delivery_queue = delivery_queue
        self.station_id = str(station_id).strip() or "local-station"

    @property
    def gateway_id(self) -> str:
        return self.broker.gateway_id

    @property
    def supported_networks(self) -> set[str]:
        return {"meshtastic"}

    def get_status(self) -> dict[str, Any]:
        return self.broker.get_status()

    def get_details(self) -> dict[str, Any]:
        return self.broker.get_details()

    def list_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        del cursor
        return self.broker.drain_inbound_events(limit=limit)

    def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.broker.list_events(limit=limit)

    def send_group_message(self, *, text: str, channel_index: int | None = None) -> dict[str, Any]:
        return self.broker.send_text(
            text=text,
            destination_id="^all",
            channel_index=channel_index,
            want_ack=False,
        )

    def send_directed_private(self, request: DirectedPrivateDeliveryRequest) -> DeliveryResult:
        destination = request.destination if isinstance(request.destination, dict) else {}
        native_address = str(
            destination.get("native_address")
            or destination.get("node_id")
            or destination.get("public_key")
            or ""
        ).strip()
        result = self.broker.send_text(
            text=str((request.payload or {}).get("text") or ""),
            destination_id=native_address,
            channel_index=0,
            want_ack=True,
        )
        return DeliveryResult(
            delivery_id=request.delivery_id,
            gateway_id=self.gateway_id,
            network="meshtastic",
            accepted=bool(result.get("accepted")),
            status=str(result.get("status") or "failed"),
            detail=str(result.get("detail") or "").strip() or None,
            transport_message_id=str(result.get("transport_message_id") or "").strip() or None,
        )

    def dispatch_queued_messages(self, *, limit: int = 10) -> list[dict[str, Any]]:
        if self.delivery_queue is None:
            return []
        return self.delivery_queue.dispatch_route_messages(
            route_name="meshtastic",
            station_id=self.station_id,
            limit=max(1, int(limit)),
            dispatcher=self._dispatch_work_item,
        )

    def _dispatch_work_item(self, item: RouteDeliveryWorkItem) -> dict[str, Any]:
        result = self.broker.send_text(
            text=item.plaintext_body,
            destination_id=item.endpoint_address,
            channel_index=0,
            want_ack=True,
        )
        return {
            "ok": bool(result.get("accepted")),
            "provider_message_id": result.get("transport_message_id"),
            "detail": result.get("detail"),
            "retryable": result.get("status") == "unavailable",
        }