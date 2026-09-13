from __future__ import annotations

from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest
from ehn_basestation.core.events import MessageEvent, MessageEventStore, utc_now_iso
from ehn_basestation.core.message_policy import prepare_ham_plaintext
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.gateways.registry import GatewayRegistry


class DeliveryOrchestrator:
    def __init__(
        self,
        *,
        gateway_registry: GatewayRegistry,
        message_events: MessageEventStore,
        delivery_store: DeliveryStore,
        station_id: str,
    ) -> None:
        self._gateway_registry = gateway_registry
        self._message_events = message_events
        self._delivery_store = delivery_store
        self._station_id = str(station_id).strip() or "local-station"

    def send_directed_private(self, *, network: str, request: DirectedPrivateDeliveryRequest) -> DeliveryResult:
        payload = request.payload if isinstance(request.payload, dict) else {}
        plaintext = prepare_ham_plaintext(
            network=network,
            text=str(payload.get("text") or ""),
            ham_plaintext=str(payload.get("ham_plaintext")) if payload.get("ham_plaintext") is not None else None,
        )
        if not plaintext.accepted:
            result = DeliveryResult(
                delivery_id=request.delivery_id,
                gateway_id="compliance-gate",
                network=network,
                accepted=False,
                status="blocked_noncompliant",
                detail=f"Outbound {network} delivery blocked by ham plaintext gate: {plaintext.reason}.",
            )
            self._message_events.record(
                MessageEvent(
                    event_type="delivery_dispatch",
                    direction="delivery",
                    timestamp=utc_now_iso(),
                    gateway_id=None,
                    delivery_id=request.delivery_id,
                    status=result.status,
                    summary=result.detail,
                    network=network,
                    content_class=str(payload.get("content_class") or "message"),
                )
            )
            return result

        if plaintext.rendered_text is not None:
            payload = dict(payload)
            payload["text"] = plaintext.rendered_text
            request.payload = payload

        gateway = self._gateway_registry.find_provider_for_network(network)
        if gateway is None:
            return self._record_or_queue_result(
                network=network,
                request=request,
                gateway_id="unresolved",
                accepted=False,
                status="unsupported",
                detail=f"No gateway registered for network {network}.",
            )

        result = gateway.send_directed_private(request)
        return self._record_or_queue_result(
            network=network,
            request=request,
            gateway_id=result.gateway_id,
            accepted=result.accepted,
            status=result.status,
            detail=result.detail,
            transport_message_id=result.transport_message_id,
        )

    def _record_or_queue_result(
        self,
        *,
        network: str,
        request: DirectedPrivateDeliveryRequest,
        gateway_id: str,
        accepted: bool,
        status: str,
        detail: str | None,
        transport_message_id: str | None = None,
    ) -> DeliveryResult:
        payload = request.payload if isinstance(request.payload, dict) else {}
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        target_user_id = str(metadata.get("target_user_id") or metadata.get("target_id") or "").strip()
        queueable_statuses = {"unsupported", "unavailable", "rejected", "offline", "timeout", "failed"}

        if (not accepted) and target_user_id and str(status).strip().lower() in queueable_statuses and str(payload.get("text") or "").strip():
            destination = request.destination if isinstance(request.destination, dict) else {}
            queued = self._delivery_store.enqueue_message(
                sender_user_id=str(metadata.get("source_user_id") or "system").strip() or "system",
                target_user_id=target_user_id,
                target_endpoint_network=str(network).strip().lower(),
                target_endpoint_address=str(
                    destination.get("native_address")
                    or destination.get("phone_number")
                    or destination.get("to_number")
                    or ""
                ).strip(),
                conversation_scope=str(metadata.get("conversation_scope") or "single").strip() or "single",
                message_class=str(payload.get("content_class") or "message").strip() or "message",
                plaintext_body=str(payload.get("text") or ""),
                body_summary=str(detail or "").strip(),
                accepted_station_id=self._station_id,
                owning_station_id=self._station_id,
                selected_route=str(network).strip(),
                created_by_event_id=request.delivery_id,
            )
            result = DeliveryResult(
                delivery_id=request.delivery_id,
                gateway_id=gateway_id,
                network=network,
                accepted=True,
                status="accepted_but_queued",
                detail=f"Immediate {network} delivery unavailable; message stored for later delivery.",
                transport_message_id=queued.message_id,
            )
        else:
            result = DeliveryResult(
                delivery_id=request.delivery_id,
                gateway_id=gateway_id,
                network=network,
                accepted=accepted,
                status=status,
                detail=detail,
                transport_message_id=transport_message_id,
            )

        self._message_events.record(
            MessageEvent(
                event_type="delivery_dispatch",
                direction="delivery",
                timestamp=utc_now_iso(),
                gateway_id=gateway_id or None,
                delivery_id=result.delivery_id,
                status=result.status,
                summary=result.detail,
                network=network,
                sender_user_id=str(metadata.get("source_user_id") or "").strip() or None,
                target_id=target_user_id or None,
                content_class=str(payload.get("content_class") or "message"),
            )
        )
        return result
