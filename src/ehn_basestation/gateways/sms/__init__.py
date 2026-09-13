from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest, GatewayAdapter
from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.core.events import utc_now_iso


class AndroidSmsGatewayAdapter(GatewayAdapter):
	def __init__(self, *, gateway_id: str, display_name: str, delivery_queue: DeliveryQueueService, station_id: str) -> None:
		self._gateway_id = str(gateway_id).strip() or "android-sms-gateway-1"
		self._display_name = str(display_name).strip() or "Android SMS Gateway"
		self._delivery_queue = delivery_queue
		self._station_id = str(station_id).strip() or "local-station"
		self._lock = Lock()
		self._inbound_events: list[dict[str, Any]] = []
		self._last_android_seen_at: str | None = None
		self._last_link_status_at: str | None = None
		self._last_outbound_claim_at: str | None = None
		self._last_outbound_completion_at: str | None = None
		self._last_inbound_received_at: str | None = None
		self._last_android_request_at: str | None = None
		self._last_android_request_path: str | None = None
		self._last_android_request_authorized: bool | None = None
		self._last_android_request_base_url: str | None = None
		self._last_android_request_remote_addr: str | None = None

	@property
	def gateway_id(self) -> str:
		return self._gateway_id

	@property
	def supported_networks(self) -> set[str]:
		return {"sms", "rcs"}

	def get_status(self) -> dict[str, Any]:
		outbound_items = self.list_outbound_messages(limit=500)
		with self._lock:
			queued = sum(1 for item in outbound_items if str(item.get("state") or "") == "queued")
			claimed = sum(1 for item in outbound_items if str(item.get("state") or "") == "claimed")
			sent = sum(1 for item in outbound_items if str(item.get("state") or "") == "delivered")
			failed = sum(1 for item in outbound_items if str(item.get("state") or "") == "failed")
			last_android_seen_at = self._last_android_seen_at
			last_link_status_at = self._last_link_status_at
			last_outbound_claim_at = self._last_outbound_claim_at
			last_outbound_completion_at = self._last_outbound_completion_at
			last_inbound_received_at = self._last_inbound_received_at
			last_android_request_at = self._last_android_request_at
			last_android_request_path = self._last_android_request_path
			last_android_request_authorized = self._last_android_request_authorized
			last_android_request_base_url = self._last_android_request_base_url
			last_android_request_remote_addr = self._last_android_request_remote_addr
		link_state = _derive_phone_link_state(last_android_seen_at)
		status = _derive_gateway_status(
			link_state=link_state,
			queued=queued,
			claimed=claimed,
			failed=failed,
		)
		return {
			"gateway_id": self._gateway_id,
			"display_name": self._display_name,
			"network": "sms",
			"transport": "android-http-edge",
			"status": status,
			"phone_link_state": link_state,
			"last_android_seen_at": last_android_seen_at,
			"last_link_status_at": last_link_status_at,
			"last_outbound_claim_at": last_outbound_claim_at,
			"last_outbound_completion_at": last_outbound_completion_at,
			"last_inbound_received_at": last_inbound_received_at,
			"last_android_request_at": last_android_request_at,
			"last_android_request_path": last_android_request_path,
			"last_android_request_authorized": last_android_request_authorized,
			"last_android_request_base_url": last_android_request_base_url,
			"last_android_request_remote_addr": last_android_request_remote_addr,
			"queued_outbound": queued,
			"claimed_outbound": claimed,
			"sent_outbound": sent,
			"failed_outbound": failed,
		}

	def note_android_request(
		self,
		*,
		path: str,
		authorized: bool,
		observed_base_url: str | None,
		remote_addr: str | None,
	) -> str:
		timestamp = utc_now_iso()
		with self._lock:
			self._last_android_request_at = timestamp
			self._last_android_request_path = str(path or "").strip() or None
			self._last_android_request_authorized = bool(authorized)
			self._last_android_request_base_url = str(observed_base_url or "").strip() or None
			self._last_android_request_remote_addr = str(remote_addr or "").strip() or None
		return timestamp

	def list_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
		with self._lock:
			events = list(self._inbound_events)
		return events[:limit]

	def find_recent_duplicate_notification_event(
		self,
		*,
		body: str,
		transport: str,
		observed_at_utc: str,
		window_seconds: int = 120,
	) -> dict[str, Any] | None:
		normalized_transport = str(transport or "").strip().lower()
		if normalized_transport != "rcs":
			return None
		normalized_body = str(body or "").strip()
		if not normalized_body:
			return None
		observed_at = _parse_iso_datetime(observed_at_utc)
		with self._lock:
			for event in self._inbound_events[:20]:
				if str(event.get("network") or "").strip().lower() != "sms":
					continue
				if str(event.get("body") or "").strip() != normalized_body:
					continue
				event_observed_at = _parse_iso_datetime(str(event.get("observed_at_utc") or event.get("event_ts_utc") or ""))
				if observed_at is not None and event_observed_at is not None:
					delta_seconds = abs((observed_at - event_observed_at).total_seconds())
					if delta_seconds > window_seconds:
						continue
				return dict(event)
		return None

	def list_outbound_messages(self, *, limit: int = 50) -> list[dict[str, Any]]:
		payload: list[dict[str, Any]] = []
		items = self._delivery_queue.list_route_messages(route_name="sms", include_content=True, limit=max(1, int(limit)))
		for item in items:
			payload.append(
				{
					"message_id": item.get("message_id"),
					"to_number": item.get("target_endpoint_address"),
					"body": item.get("plaintext_body"),
					"requested_at": item.get("created_at"),
					"claimed_at": item.get("claim_heartbeat_at") or item.get("updated_at"),
					"updated_at": item.get("updated_at"),
					"state": item.get("delivery_state"),
					"provider_message_id": None,
					"detail": item.get("body_summary") or item.get("last_error_code"),
				}
			)
		return payload

	def send_directed_private(self, request: DirectedPrivateDeliveryRequest) -> DeliveryResult:
		destination = request.destination if isinstance(request.destination, dict) else {}
		payload = request.payload if isinstance(request.payload, dict) else {}
		to_number = str(
			destination.get("native_address")
			or destination.get("phone_number")
			or destination.get("to_number")
			or ""
		).strip()
		text = str(payload.get("text") or "").strip()
		if not to_number:
			return DeliveryResult(
				delivery_id=request.delivery_id,
				gateway_id=self._gateway_id,
				network="sms",
				accepted=False,
				status="invalid_destination",
				detail="SMS destination number is required.",
			)
		if not text:
			return DeliveryResult(
				delivery_id=request.delivery_id,
				gateway_id=self._gateway_id,
				network="sms",
				accepted=False,
				status="missing_text",
				detail="SMS delivery text is required.",
			)

		queued = self.queue_outbound_sms(
			to_number=to_number,
			body=text,
			target_user_id=str(request.metadata.get("target_user_id") or request.metadata.get("target_id") or "").strip(),
			created_by_event_id=request.delivery_id,
		)
		return DeliveryResult(
			delivery_id=request.delivery_id,
			gateway_id=self._gateway_id,
			network="sms",
			accepted=True,
			status="queued",
			detail=f"Queued outbound SMS for {to_number}.",
			transport_message_id=queued.message_id,
		)

	def note_android_seen(self) -> str:
		timestamp = utc_now_iso()
		with self._lock:
			self._last_android_seen_at = timestamp
			self._last_link_status_at = timestamp
		return timestamp

	def record_inbound_message(
		self,
		*,
		from_number: str | None,
		from_name: str | None,
		body: str,
		transport: str,
		observed_at_utc: str,
	) -> dict[str, Any]:
		event = {
			"event_id": f"android-in-{uuid4().hex}",
			"gateway_id": self._gateway_id,
			"network": str(transport or "sms").strip().lower() or "sms",
			"from_number": from_number,
			"from_name": from_name,
			"phone_number": from_number,
			"source_endpoint": from_number or from_name,
			"body": body,
			"text": body,
			"observed_at_utc": observed_at_utc,
			"event_ts_utc": observed_at_utc,
			"message_class": "direct",
		}
		with self._lock:
			self._last_android_seen_at = utc_now_iso()
			self._last_inbound_received_at = observed_at_utc
			self._inbound_events.insert(0, event)
			del self._inbound_events[200:]
		return event

	def queue_outbound_sms(
		self,
		*,
		to_number: str,
		body: str,
		target_user_id: str = "",
		created_by_event_id: str = "",
	) -> Any:
		return self._delivery_queue.enqueue_route_message(
			sender_user_id="system",
			target_user_id=str(target_user_id).strip(),
			route_name="sms",
			endpoint_network="sms",
			endpoint_address=str(to_number).strip(),
			message_class="system_reply",
			plaintext_body=str(body),
			accepted_station_id=self._station_id,
			owning_station_id=self._station_id,
			conversation_scope="single",
			created_by_event_id=str(created_by_event_id).strip(),
		)

	def claim_outbound_messages(self, *, limit: int = 10) -> list[dict[str, str]]:
		claimed: list[dict[str, str]] = []
		claimed_records = self._delivery_queue.claim_route_messages(
			route_name="sms",
			station_id=self._station_id,
			limit=max(1, int(limit)),
		)
		with self._lock:
			self._last_android_seen_at = utc_now_iso()
			for item in claimed_records:
				self._last_outbound_claim_at = utc_now_iso()
				claimed.append(item.as_transport_payload())
		return claimed

	def complete_outbound_message(
		self,
		*,
		message_id: str,
		ok: bool,
		provider_message_id: str | None,
		detail: str | None,
	) -> dict[str, Any] | None:
		completion = self._delivery_queue.complete_route_message(
			route_name="sms",
			station_id=self._station_id,
			message_id=str(message_id).strip(),
			ok=ok,
			provider_message_id=provider_message_id,
			detail=detail,
		)
		if completion is None:
			return None
		completed_at = utc_now_iso()
		with self._lock:
			self._last_android_seen_at = completed_at
			self._last_outbound_completion_at = completed_at
		return completion


def _parse_iso_datetime(value: str) -> datetime | None:
	text = str(value or "").strip()
	if not text:
		return None
	try:
		return datetime.fromisoformat(text.replace("Z", "+00:00"))
	except ValueError:
		return None


def _seconds_since(timestamp: str | None) -> float | None:
	parsed = _parse_iso_datetime(str(timestamp or ""))
	if parsed is None:
		return None
	now = datetime.now(timezone.utc)
	return max(0.0, (now - parsed).total_seconds())


def _derive_phone_link_state(last_android_seen_at: str | None) -> str:
	seconds = _seconds_since(last_android_seen_at)
	if seconds is None:
		return "never_seen"
	if seconds <= 90:
		return "connected"
	if seconds <= 300:
		return "stale"
	return "offline"


def _derive_gateway_status(*, link_state: str, queued: int, claimed: int, failed: int) -> str:
	if link_state == "never_seen":
		return "waiting_for_phone"
	if link_state in {"stale", "offline"} and queued > 0:
		return "queue_waiting_for_phone"
	if link_state in {"stale", "offline"}:
		return "phone_stale"
	if claimed > 0:
		return "phone_sending"
	if queued > 0:
		return "queue_pending"
	if failed > 0:
		return "delivery_failed"
	return "connected_idle"
