from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ehn_basestation.core.delivery_queue import DeliveryQueueService, RouteDeliveryWorkItem
from ehn_basestation.core.events import MessageEvent, MessageEventStore, utc_now_iso
from ehn_basestation.directory.delivery_store import DeliveryStore, SinkRecord
from ehn_basestation.sinks import SinkAdapter, SinkDeliveryItem

SINK_ROUTE_NAME = "sink"
SINK_TARGET_PREFIX = "sink:"


def sink_target_id(sink_id: str) -> str:
    return f"{SINK_TARGET_PREFIX}{str(sink_id).strip().lower()}"


def sink_id_from_target(target_id: str) -> str | None:
    text = str(target_id or "").strip()
    if not text.startswith(SINK_TARGET_PREFIX):
        return None
    return text[len(SINK_TARGET_PREFIX):] or None


@dataclass(frozen=True, slots=True)
class SinkSubmissionResult:
    ok: bool
    accepted: bool
    status: str
    detail: str
    sink_id: str
    queued_message_ids: tuple[str, ...] = ()
    dispatch_results: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "accepted": self.accepted,
            "mode": "sink",
            "network": SINK_ROUTE_NAME,
            "status": self.status,
            "detail": self.detail,
            "target_id": self.sink_id,
            "queued_message_ids": list(self.queued_message_ids),
            "queued_count": len(self.queued_message_ids),
            "dispatch_results": list(self.dispatch_results),
        }


class SinkDeliveryService:
    """Validates `#sink-id` submissions, fans them out as queued rows, and dispatches through adapters."""

    def __init__(
        self,
        *,
        delivery_store: DeliveryStore,
        delivery_queue: DeliveryQueueService,
        message_events: MessageEventStore,
        station_id: str,
        adapters: dict[str, SinkAdapter] | None = None,
    ) -> None:
        self._delivery_store = delivery_store
        self._delivery_queue = delivery_queue
        self._message_events = message_events
        self._station_id = str(station_id or "").strip() or "local-station"
        self._adapters: dict[str, SinkAdapter] = dict(adapters or {})

    @property
    def adapter_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def submit(
        self,
        *,
        sink_id: str,
        body: str,
        sender_user_id: str | None,
        actor_class: str,
        content_class: str,
        source_network: str,
        source_gateway_id: str,
        created_by_event_id: str = "",
        dispatch_now: bool = True,
    ) -> SinkSubmissionResult:
        normalized_sink_id = str(sink_id or "").strip().lower()
        sink = self._delivery_store.get_sink(normalized_sink_id)
        rejection = self._validate(sink, normalized_sink_id, body=body, actor_class=actor_class, content_class=content_class)
        if rejection is not None:
            self._record_event(
                status=rejection.status,
                sink_id=normalized_sink_id,
                summary=rejection.detail,
                network=source_network,
                gateway_id=source_gateway_id,
                sender_user_id=sender_user_id,
                content_class=content_class,
                actor_class=actor_class,
            )
            return rejection

        endpoints = self._delivery_store.list_sink_endpoints(normalized_sink_id, enabled_only=True)
        if not endpoints:
            result = SinkSubmissionResult(
                ok=False,
                accepted=False,
                status="sink_has_no_endpoints",
                detail=f"Sink {normalized_sink_id} has no enabled delivery endpoints.",
                sink_id=normalized_sink_id,
            )
            self._record_event(
                status=result.status,
                sink_id=normalized_sink_id,
                summary=result.detail,
                network=source_network,
                gateway_id=source_gateway_id,
                sender_user_id=sender_user_id,
                content_class=content_class,
                actor_class=actor_class,
            )
            return result

        queued_ids: list[str] = []
        for endpoint in endpoints:
            record = self._delivery_queue.enqueue_route_message(
                sender_user_id=str(sender_user_id or "").strip() or "unknown",
                target_user_id=sink_target_id(normalized_sink_id),
                route_name=SINK_ROUTE_NAME,
                endpoint_network=endpoint.adapter_type,
                endpoint_address=endpoint.sink_endpoint_id,
                plaintext_body=str(body),
                accepted_station_id=self._station_id,
                owning_station_id=self._station_id,
                message_class=str(content_class or "telemetry").strip() or "telemetry",
                conversation_scope="sink",
                body_summary=f"sink {normalized_sink_id} via {endpoint.adapter_type}",
                created_by_event_id=created_by_event_id,
            )
            queued_ids.append(record.message_id)

        self._record_event(
            status="accepted_and_stored",
            sink_id=normalized_sink_id,
            summary=f"Stored sink submission for {normalized_sink_id}; {len(queued_ids)} endpoint deliveries queued.",
            network=source_network,
            gateway_id=source_gateway_id,
            sender_user_id=sender_user_id,
            content_class=content_class,
            actor_class=actor_class,
        )

        dispatch_results: list[dict[str, Any]] = []
        if dispatch_now:
            dispatch_results = self.dispatch(limit=len(queued_ids))
        delivered = sum(1 for item in dispatch_results if item.get("dispatch_ok"))
        status = "delivered" if queued_ids and delivered == len(queued_ids) else "accepted_and_stored"
        return SinkSubmissionResult(
            ok=True,
            accepted=True,
            status=status,
            detail=f"{delivered} of {len(queued_ids)} sink deliveries completed immediately.",
            sink_id=normalized_sink_id,
            queued_message_ids=tuple(queued_ids),
            dispatch_results=tuple(dispatch_results),
        )

    def dispatch(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return self._delivery_queue.dispatch_route_messages(
            route_name=SINK_ROUTE_NAME,
            station_id=self._station_id,
            limit=max(1, int(limit)),
            dispatcher=self._dispatch_item,
        )

    def _dispatch_item(self, item: RouteDeliveryWorkItem) -> dict[str, Any]:
        endpoint = self._delivery_store.get_sink_endpoint(item.endpoint_address)
        if endpoint is None or not endpoint.enabled:
            return {"ok": False, "detail": "sink endpoint missing or disabled", "retryable": False}
        adapter = self._adapters.get(endpoint.adapter_type)
        if adapter is None:
            return {"ok": False, "detail": f"no adapter for {endpoint.adapter_type}", "retryable": False}
        delivery_item = SinkDeliveryItem(
            message_id=item.message_id,
            sink_id=sink_id_from_target(item.target_user_id) or endpoint.sink_id,
            sender_user_id=item.sender_user_id,
            content_class=item.message_class,
            body=item.plaintext_body,
            created_at=item.created_at,
        )
        outcome = adapter.deliver(endpoint, delivery_item)
        if outcome.ok:
            self._delivery_store.mark_sink_endpoint_delivered(endpoint.sink_endpoint_id)
        return outcome.as_dict()

    def _validate(
        self,
        sink: SinkRecord | None,
        sink_id: str,
        *,
        body: str,
        actor_class: str,
        content_class: str,
    ) -> SinkSubmissionResult | None:
        def rejected(status: str, detail: str) -> SinkSubmissionResult:
            return SinkSubmissionResult(ok=False, accepted=False, status=status, detail=detail, sink_id=sink_id)

        if sink is None:
            return rejected("unknown_sink", f"Sink {sink_id} is not configured.")
        if not sink.enabled:
            return rejected("sink_disabled", f"Sink {sink_id} is disabled.")
        normalized_actor = str(actor_class or "person").strip().lower()
        if sink.accepted_actor_classes and normalized_actor not in sink.accepted_actor_classes:
            return rejected("actor_not_accepted", f"Sink {sink_id} does not accept {normalized_actor} senders.")
        normalized_content = str(content_class or "message").strip().lower()
        if sink.accepted_content_classes and normalized_content not in sink.accepted_content_classes:
            return rejected("content_not_accepted", f"Sink {sink_id} does not accept {normalized_content} content.")
        if len(str(body or "")) > sink.max_body_chars:
            return rejected("body_too_long", f"Submission exceeds {sink.max_body_chars} characters for sink {sink_id}.")
        return None

    def _record_event(
        self,
        *,
        status: str,
        sink_id: str,
        summary: str,
        network: str,
        gateway_id: str,
        sender_user_id: str | None,
        content_class: str,
        actor_class: str,
    ) -> None:
        self._message_events.record(
            MessageEvent(
                event_type="sink_submission",
                direction="egress",
                timestamp=utc_now_iso(),
                gateway_id=str(gateway_id or "").strip() or "basestation",
                status=status,
                summary=summary[:120],
                network=SINK_ROUTE_NAME,
                sender_endpoint=None,
                sender_user_id=str(sender_user_id or "").strip() or None,
                conversation_scope="sink",
                target_id=sink_target_id(sink_id),
                actor_class=actor_class,
                content_class=content_class,
            )
        )
