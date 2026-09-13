from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import threading

from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.core.events import MessageEvent, MessageEventStore, utc_now_iso
from ehn_basestation.core.presence import PresenceService, canonical_endpoint_network
from ehn_basestation.core.reply_templates import ReplyTemplateResolver
from ehn_basestation.directory.delivery_store import DeliveryStore, PendingMessageRecord
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.gateways.registry import GatewayRegistry


@dataclass(frozen=True, slots=True)
class MaintenanceRunSummary:
    ran_at: str
    released_claims: int
    expired: int
    expiry_notices_queued: int
    presence_observations: int
    dispatched_by_route: dict[str, int]
    delivered_by_route: dict[str, int]
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at,
            "released_claims": self.released_claims,
            "expired": self.expired,
            "expiry_notices_queued": self.expiry_notices_queued,
            "presence_observations": self.presence_observations,
            "dispatched_by_route": dict(self.dispatched_by_route),
            "delivered_by_route": dict(self.delivered_by_route),
            "errors": list(self.errors),
        }


class DeliveryMaintenanceService:
    """One periodic tick: expire, release stale claims, sweep gateway presence, dispatch every route."""

    def __init__(
        self,
        *,
        delivery_store: DeliveryStore,
        delivery_queue: DeliveryQueueService,
        presence_service: PresenceService,
        gateway_registry: GatewayRegistry,
        local_directory: DirectoryStore,
        reply_templates: ReplyTemplateResolver,
        message_events: MessageEventStore,
        station_id: str,
        extra_dispatchers: dict[str, Callable[[int], list[dict[str, Any]]]] | None = None,
        dispatch_limit: int = 20,
    ) -> None:
        self._delivery_store = delivery_store
        self._delivery_queue = delivery_queue
        self._presence_service = presence_service
        self._gateway_registry = gateway_registry
        self._local_directory = local_directory
        self._reply_templates = reply_templates
        self._message_events = message_events
        self._station_id = str(station_id or "").strip() or "local-station"
        self._extra_dispatchers = dict(extra_dispatchers or {})
        self._dispatch_limit = max(1, int(dispatch_limit))
        self._lock = threading.Lock()
        self.last_run: MaintenanceRunSummary | None = None

    def run_once(self, *, now: str | None = None) -> MaintenanceRunSummary:
        # Serialize ticks so the scheduler and a manual POST cannot overlap.
        with self._lock:
            errors: list[str] = []
            queue_result = self._delivery_queue.run_maintenance(station_id=self._station_id, now=now)
            expired: list[PendingMessageRecord] = list(queue_result.get("expired") or [])
            notices = 0
            for record in expired:
                try:
                    if self._queue_expiry_notice(record):
                        notices += 1
                except Exception as exc:  # keep the tick alive; the row is already expired
                    errors.append(f"expiry notice {record.message_id}: {exc}")

            observations = []
            try:
                observations = self._presence_service.sweep_gateway_observations(dispatch=False)
            except Exception as exc:
                errors.append(f"presence sweep: {exc}")

            dispatched: dict[str, int] = {}
            delivered: dict[str, int] = {}
            for route_name, dispatcher in self._collect_dispatchers().items():
                try:
                    results = list(dispatcher(self._dispatch_limit) or [])
                except Exception as exc:
                    errors.append(f"dispatch {route_name}: {exc}")
                    continue
                if results:
                    dispatched[route_name] = len(results)
                    delivered[route_name] = sum(1 for item in results if item.get("dispatch_ok"))

            summary = MaintenanceRunSummary(
                ran_at=str(queue_result.get("ran_at") or utc_now_iso()),
                released_claims=len(queue_result.get("released_claims") or []),
                expired=len(expired),
                expiry_notices_queued=notices,
                presence_observations=len(observations),
                dispatched_by_route=dispatched,
                delivered_by_route=delivered,
                errors=tuple(errors),
            )
            self.last_run = summary
            if summary.expired or summary.released_claims or dispatched or errors:
                self._message_events.record(
                    MessageEvent(
                        event_type="delivery_maintenance",
                        direction="egress",
                        timestamp=summary.ran_at,
                        gateway_id=self._station_id,
                        status="errors" if errors else "ok",
                        summary=(
                            f"expired={summary.expired} released={summary.released_claims} "
                            f"dispatched={sum(dispatched.values())} delivered={sum(delivered.values())}"
                        ),
                        network="basestation",
                    )
                )
            return summary

    def _collect_dispatchers(self) -> dict[str, Callable[[int], list[dict[str, Any]]]]:
        dispatchers: dict[str, Callable[[int], list[dict[str, Any]]]] = {}
        for gateway in self._gateway_registry.list_providers():
            dispatch = getattr(gateway, "dispatch_queued_messages", None)
            if not callable(dispatch):
                continue
            for network in sorted(getattr(gateway, "supported_networks", set()) or set()):
                route = canonical_endpoint_network(network)
                dispatchers.setdefault(route, lambda limit, _dispatch=dispatch: _dispatch(limit=limit))
        dispatchers.update(self._extra_dispatchers)
        return dispatchers

    def _queue_expiry_notice(self, record: PendingMessageRecord) -> bool:
        """Tell the original sender their stored message expired, using their SMS endpoint when they have one."""
        sender_id = str(record.sender_user_id or "").strip()
        if not sender_id or sender_id in {"system", "unknown"} or sender_id.startswith("sink:"):
            return False
        if str(record.target_user_id or "").startswith("sink:"):
            return False
        sender = self._local_directory.get_user_by_id(sender_id)
        if sender is None:
            return False
        sms_gateway = self._gateway_registry.find_provider_for_network("sms")
        queue_outbound_sms = getattr(sms_gateway, "queue_outbound_sms", None)
        if not callable(queue_outbound_sms):
            return False
        phone = next(
            (
                str(endpoint.native_address or "").strip()
                for endpoint in sorted(sender.endpoints, key=lambda item: -int(item.priority or 0))
                if canonical_endpoint_network(endpoint.network) == "sms"
                and str(endpoint.native_address or "").strip()
                and str(endpoint.enabled).lower() not in {"false", "0"}
            ),
            "",
        )
        if not phone:
            return False
        language = str((sender.profile.get("personal") or {}).get("preferred_language") or "").strip() or None
        body = self._reply_templates.render("delivery_expired", preferred_language=language)
        queue_outbound_sms(to_number=phone, body=body)
        return True


class MaintenanceScheduler:
    """Daemon thread that runs maintenance on a fixed interval until stopped."""

    def __init__(self, service: DeliveryMaintenanceService, *, interval_seconds: int) -> None:
        self._service = service
        self._interval = max(5, int(interval_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="ehn-delivery-maintenance", daemon=True)

    @property
    def interval_seconds(self) -> int:
        return self._interval

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._service.run_once()
            except Exception:
                # A failed tick must not kill the scheduler; the next tick retries.
                continue
