from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ehn_basestation.core.events import MessageEvent, MessageEventStore, utc_now_iso
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore, LocalUserEntry
from ehn_basestation.gateways.registry import GatewayRegistry


def canonical_endpoint_network(network: str) -> str:
    normalized = str(network or "").strip().lower()
    if normalized in {"sms", "rcs"}:
        return "sms"
    if normalized.startswith("meshcore"):
        return "meshcore"
    if normalized.startswith("meshtastic"):
        return "meshtastic"
    return normalized


@dataclass(frozen=True, slots=True)
class PresenceObservation:
    user_id: str
    network: str
    evidence: str
    observed_at: str
    endpoint_id: str
    endpoint_address: str
    refreshed_message_ids: tuple[str, ...] = ()
    dispatch_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "network": self.network,
            "evidence": self.evidence,
            "observed_at": self.observed_at,
            "endpoint_id": self.endpoint_id,
            "has_endpoint_address": bool(self.endpoint_address),
            "refreshed_count": len(self.refreshed_message_ids),
            "dispatched_count": len(self.dispatch_results),
            "delivered_count": sum(1 for item in self.dispatch_results if item.get("dispatch_ok")),
        }


class PresenceService:
    """Transport-neutral presence intake.

    Any gateway that learns a user is reachable (inbound message, registration, mesh advert,
    Meshtastic node-info, phone poll) reports it here. The service records presence, re-points
    waiting queue rows at the observed route, and asks that route's adapter to dispatch.
    """

    def __init__(
        self,
        *,
        delivery_store: DeliveryStore,
        gateway_registry: GatewayRegistry,
        local_directory: DirectoryStore,
        authority_directory: DirectoryStore | None = None,
        message_events: MessageEventStore | None = None,
        station_id: str,
    ) -> None:
        self._delivery_store = delivery_store
        self._gateway_registry = gateway_registry
        self._local_directory = local_directory
        self._authority_directory = authority_directory
        self._message_events = message_events
        self._station_id = str(station_id or "").strip() or "local-station"

    def observe_user(
        self,
        *,
        user_entry: LocalUserEntry,
        network: str,
        evidence: str,
        endpoint_address: str = "",
        observed_at: str | None = None,
        presence_state: str | None = None,
        dispatch: bool = True,
        dispatch_limit: int = 10,
    ) -> PresenceObservation:
        canonical_network = canonical_endpoint_network(network)
        seen_at = str(observed_at or utc_now_iso()).strip()
        endpoint_id, resolved_address = self._match_endpoint(user_entry, canonical_network, endpoint_address)
        state = presence_state or ("registered" if evidence == "register" else "messaging" if evidence == "message" else "reachable")
        self._delivery_store.upsert_user_presence(
            user_id=user_entry.user.user_id,
            last_seen_station_id=self._station_id,
            last_seen_via_network=canonical_network,
            last_seen_at=seen_at,
            last_seen_endpoint_id=endpoint_id,
            presence_state=state,
            presence_evidence=evidence,
            updated_at=utc_now_iso(),
        )
        identity_label = str(user_entry.identity.display_name or "").strip()
        identity_aliases: tuple[str, ...] = ()
        if identity_label and identity_label.casefold() != user_entry.user.user_id.casefold():
            matches = [
                entry for entry in self._local_directory.list_users()
                if str(entry.identity.display_name or "").strip().casefold() == identity_label.casefold()
            ]
            if len(matches) == 1 and matches[0].user.user_id == user_entry.user.user_id:
                identity_aliases = (identity_label,)
        refreshed = self._delivery_store.refresh_waiting_messages_for_user(
            target_user_id=user_entry.user.user_id,
            route_name=canonical_network,
            endpoint_network=canonical_network,
            endpoint_address=resolved_address,
            target_identity_aliases=identity_aliases,
            now=utc_now_iso(),
        )
        dispatch_results: list[dict[str, Any]] = []
        if dispatch and refreshed:
            dispatch_results = self.dispatch_route(canonical_network, limit=dispatch_limit)
        observation = PresenceObservation(
            user_id=user_entry.user.user_id,
            network=canonical_network,
            evidence=evidence,
            observed_at=seen_at,
            endpoint_id=endpoint_id,
            endpoint_address=resolved_address,
            refreshed_message_ids=tuple(record.message_id for record in refreshed),
            dispatch_results=tuple(dispatch_results),
        )
        if self._message_events is not None and refreshed:
            self._message_events.record(
                MessageEvent(
                    event_type="presence_observed",
                    direction="ingress",
                    timestamp=utc_now_iso(),
                    gateway_id=self._station_id,
                    status=evidence,
                    summary=f"{user_entry.user.user_id} reachable via {canonical_network}; {len(refreshed)} queued message(s) refreshed.",
                    network=canonical_network,
                    sender_user_id=user_entry.user.user_id,
                    target_id=user_entry.user.user_id,
                )
            )
        return observation

    def observe_endpoint(
        self,
        *,
        network: str,
        native_address: str,
        evidence: str,
        observed_at: str | None = None,
        only_if_newer: bool = True,
        dispatch: bool = True,
    ) -> PresenceObservation | None:
        """Presence from a transport-native identity such as a mesh public key. Unknown endpoints are ignored."""
        canonical_network = canonical_endpoint_network(network)
        address = str(native_address or "").strip()
        if not canonical_network or not address:
            return None
        entry = self._resolve_endpoint(canonical_network, address)
        if entry is None:
            return None
        if only_if_newer and observed_at:
            current = self._delivery_store.get_user_presence(entry.user.user_id)
            if current is not None and _iso_to_dt(current.last_seen_at) is not None:
                incoming = _iso_to_dt(observed_at)
                if incoming is not None and incoming <= _iso_to_dt(current.last_seen_at):
                    return None
        return self.observe_user(
            user_entry=entry,
            network=canonical_network,
            evidence=evidence,
            endpoint_address=address,
            observed_at=observed_at,
            dispatch=dispatch,
        )

    def sweep_gateway_observations(self, *, dispatch: bool = True) -> list[PresenceObservation]:
        """Poll every registered gateway for observed candidates and treat fresh ones as presence."""
        observations: list[PresenceObservation] = []
        for gateway in self._gateway_registry.list_providers():
            list_candidates = getattr(gateway, "list_observed_candidates", None)
            if not callable(list_candidates):
                continue
            try:
                candidates = list_candidates()
            except Exception:
                continue
            for candidate in candidates or []:
                if not isinstance(candidate, dict):
                    continue
                network = str(candidate.get("network") or "").strip()
                native = candidate.get("native_identity") if isinstance(candidate.get("native_identity"), dict) else {}
                address = str(native.get("public_key") or native.get("node_id") or "").strip()
                observed_at = str(candidate.get("observed_at") or "").strip() or None
                if not network or not address or not observed_at:
                    continue
                observation = self.observe_endpoint(
                    network=network,
                    native_address=address,
                    evidence="advert",
                    observed_at=observed_at,
                    dispatch=dispatch,
                )
                if observation is not None:
                    observations.append(observation)
        return observations

    def dispatch_route(self, network: str, *, limit: int = 10) -> list[dict[str, Any]]:
        gateway = self._gateway_registry.find_provider_for_network(canonical_endpoint_network(network))
        dispatcher = getattr(gateway, "dispatch_queued_messages", None)
        if not callable(dispatcher):
            return []
        results = dispatcher(limit=max(1, int(limit)))
        return list(results or [])

    def _resolve_endpoint(self, network: str, address: str) -> LocalUserEntry | None:
        entry = self._local_directory.find_user_by_endpoint(network=network, native_address=address)
        if entry is not None:
            return entry
        authority = self._authority_directory
        if authority is None or authority is self._local_directory:
            return None
        authority_entry = authority.find_user_by_endpoint(network=network, native_address=address)
        if authority_entry is None:
            return None
        return self._local_directory.save_directory_user(authority_entry.as_dict())

    def _match_endpoint(self, entry: LocalUserEntry, network: str, address: str) -> tuple[str, str]:
        wanted = str(address or "").strip()
        fallback_id = ""
        fallback_address = ""
        for endpoint in entry.endpoints:
            if canonical_endpoint_network(endpoint.network) != network:
                continue
            native = str(endpoint.native_address or "").strip()
            if wanted and native == wanted:
                return endpoint.endpoint_id, native
            if not fallback_id and native:
                fallback_id, fallback_address = endpoint.endpoint_id, native
        if wanted:
            return fallback_id, wanted
        return fallback_id, fallback_address


def _iso_to_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
