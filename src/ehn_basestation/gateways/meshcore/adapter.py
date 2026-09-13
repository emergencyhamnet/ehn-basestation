from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest
from ehn_basestation.core.delivery_queue import DeliveryQueueService, RouteDeliveryWorkItem


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeshCoreRuntimeSurface(Protocol):
    @property
    def gateway_id(self) -> str: ...

    def get_snapshot(self) -> dict[str, Any]: ...

    def list_channels(self) -> list[dict[str, Any]]: ...

    def list_observed_nodes(self) -> list[dict[str, Any]]: ...

    def list_gateway_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]: ...

    def list_gateway_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]: ...

    def send_directed_private_message(
        self,
        *,
        recipient_public_key: str,
        text: str,
        sender_identity: dict[str, Any] | None = None,
        destination_identity: dict[str, Any] | None = None,
        delivery_id: str | None = None,
        reply_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]: ...


@dataclass(slots=True)
class MeshCoreGatewayAdapter:
    runtime_client: MeshCoreRuntimeSurface
    delivery_queue: DeliveryQueueService | None = None
    station_id: str = "local-station"

    @property
    def gateway_id(self) -> str:
        return str(self.runtime_client.gateway_id)

    @property
    def supported_networks(self) -> set[str]:
        return {"meshcore"}

    def get_status(self) -> dict[str, Any]:
        snapshot = dict(self.runtime_client.get_snapshot())
        runtime_status = snapshot.get("runtime_status") if isinstance(snapshot.get("runtime_status"), dict) else {}
        identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}
        channels = self.runtime_client.list_channels()
        transport = str(runtime_status.get("transport") or "meshcore-runtime")
        observed_nodes = self.runtime_client.list_observed_nodes()
        observed_node_count = snapshot.get("observed_nodes_count")
        if not isinstance(observed_node_count, int):
            observed_node_count = len(observed_nodes)

        return {
            "gateway_id": self.gateway_id,
            "gateway_type": "mesh",
            "transport": transport,
            "display_name": snapshot.get("display_name") or self.gateway_id,
            "connected": bool(snapshot.get("connected")),
            "status": snapshot.get("status"),
            "region": snapshot.get("region"),
            "management": {
                "embedded": False,
                "ui_url": snapshot.get("base_url"),
                "api_base_url": snapshot.get("base_url"),
                "mode": runtime_status.get("interface_mode") or runtime_status.get("mode"),
                "helper_tx_allowed": self._helper_tx_allowed(runtime_status=runtime_status, channels=channels),
            },
            "details": {
                "runtime": snapshot,
                "channels": channels,
                "observed_nodes": observed_node_count,
                "identity": identity,
                "message": "MeshCore gateway remains transport-native behind the basestation adapter boundary.",
            },
        }

    def get_provisioning_state(self) -> dict[str, Any]:
        snapshot = dict(self.runtime_client.get_snapshot())
        return {
            "gateway_id": self.gateway_id,
            "network": "meshcore",
            "identity": snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {},
            "channels": self.runtime_client.list_channels(),
            "contacts": self.runtime_client.list_observed_nodes(),
            "runtime_status": snapshot.get("runtime_status") if isinstance(snapshot.get("runtime_status"), dict) else {},
        }

    def list_observed_candidates(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for node in self.runtime_client.list_observed_nodes():
            node_id = str(node.get("node_id") or "").strip()
            public_key = str(node.get("public_key") or "").strip() or None
            candidate_key = public_key or node_id
            if not candidate_key:
                continue
            identity = node.get("identity") if isinstance(node.get("identity"), dict) else None
            candidates.append(
                {
                    "candidate_id": f"meshcore:{self.gateway_id}:{'public_key' if public_key else 'node'}:{candidate_key}",
                    "gateway_id": self.gateway_id,
                    "network": "meshcore",
                    "observed_at": str(node.get("last_seen_at") or node.get("last_observation_utc") or "").strip() or None,
                    "native_identity": {
                        "public_key": public_key,
                        "node_id": node_id or None,
                    },
                    "display": {
                        "contact_name": node.get("contact_name") or node.get("short_name"),
                        "observed_name": node.get("long_name") or node.get("short_name"),
                    },
                    "presence": {
                        "state": node.get("presence_state"),
                        "last_channel_name": node.get("last_channel_name"),
                    },
                    "bind_status": "bound" if identity else "unbound",
                    "identity": identity,
                    "raw": dict(node),
                }
            )
        return candidates

    def list_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        return self.runtime_client.list_gateway_inbound_events(limit=limit, cursor=cursor)

    def get_outbound_status(self, request_id: str) -> dict[str, Any] | None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return None
        for item in self.runtime_client.list_gateway_outbound_status(limit=100):
            if str(item.get("request_id") or "").strip() == normalized_request_id:
                return item
        return None

    def list_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        return self.runtime_client.list_gateway_outbound_status(limit=limit, state=state)

    def dispatch_queued_messages(self, *, limit: int = 10) -> list[dict[str, Any]]:
        if self.delivery_queue is None:
            return []
        return self.delivery_queue.dispatch_route_messages(
            route_name="meshcore",
            station_id=self.station_id,
            limit=max(1, int(limit)),
            dispatcher=self._dispatch_work_item,
        )

    def send_directed_private(self, request: DirectedPrivateDeliveryRequest) -> DeliveryResult:
        destination = request.destination if isinstance(request.destination, dict) else {}
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        public_key = str(destination.get("native_address") or destination.get("public_key") or "").strip()
        destination_identity = dict(destination)
        target_user_id = str(metadata.get("target_user_id") or metadata.get("target_id") or "").strip()
        target_identity_label = str(metadata.get("target_identity_label") or "").strip()
        if target_user_id:
            destination_identity["user_id"] = target_user_id
        if target_identity_label:
            destination_identity["display_name"] = target_identity_label
        accepted, message, payload = self.runtime_client.send_directed_private_message(
            recipient_public_key=public_key,
            text=str((request.payload or {}).get("text") or ""),
            sender_identity=request.source_identity,
            destination_identity=destination_identity,
            delivery_id=request.delivery_id,
            reply_context=dict(metadata),
        )
        payload = payload if isinstance(payload, dict) else {}
        return DeliveryResult(
            delivery_id=request.delivery_id,
            gateway_id=self.gateway_id,
            network="meshcore",
            accepted=accepted,
            status=str(payload.get("status") or ("sent" if accepted else "rejected")),
            detail=None if accepted else (str(payload.get("warning") or message).strip() or message),
            transport_message_id=str(payload.get("message_id") or payload.get("transport_message_id") or "").strip() or None,
        )

    def _dispatch_work_item(self, item: RouteDeliveryWorkItem) -> dict[str, Any]:
        accepted, message, payload = self.runtime_client.send_directed_private_message(
            recipient_public_key=item.endpoint_address,
            text=item.plaintext_body,
            delivery_id=item.message_id,
            reply_context={
                "target_user_id": item.target_user_id,
                "source_user_id": item.sender_user_id,
                "message_class": item.message_class,
                "conversation_scope": item.conversation_scope,
                "queued_delivery": True,
            },
        )
        response = payload if isinstance(payload, dict) else {}
        status = str(response.get("status") or ("sent" if accepted else "failed")).strip().lower()
        retryable = status not in {"invalid_destination", "missing_text", "blocked_noncompliant", "cancelled"}
        return {
            "ok": accepted,
            "detail": str(response.get("warning") or message or status).strip() or None,
            "provider_message_id": str(response.get("message_id") or response.get("transport_message_id") or "").strip() or None,
            "retryable": retryable if not accepted else None,
        }

    def _helper_tx_allowed(self, *, runtime_status: dict[str, Any], channels: list[dict[str, Any]]) -> bool:
        value = runtime_status.get("helper_tx_allowed")
        if value is None:
            value = runtime_status.get("browser_send_allowed")
        if value is None:
            value = runtime_status.get("tx_allowed")
        if value is None:
            value = any(bool(channel.get("browser_send_allowed")) for channel in channels if isinstance(channel, dict))
        return bool(value)
