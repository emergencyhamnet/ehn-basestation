from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ehn_basestation.core.events import MessageEvent, MessageEventStore, utc_now_iso
from ehn_basestation.core.message_policy import (
    ClassifiedInboundMessage,
    InboundTextMessage,
    MessageTarget,
    classify_inbound_text_message,
)
from ehn_basestation.core.presence import PresenceService
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore, LocalUserEntry
from ehn_basestation.gateways.registry import GatewayRegistry


def _canonical_endpoint_network(network: str) -> str:
    normalized = str(network or "").strip().lower()
    if normalized in {"sms", "rcs"}:
        return "sms"
    return normalized


def _supports_rcs_name_lookup(network: str) -> bool:
    return str(network or "").strip().lower() == "rcs"


@dataclass(frozen=True, slots=True)
class InboundProcessingResult:
    classification: ClassifiedInboundMessage
    sender_entry: LocalUserEntry | None
    target_entry: LocalUserEntry | None
    sender_resolution: str


class InboundMessageRouter:
    def __init__(
        self,
        *,
        gateway_registry: GatewayRegistry,
        message_events: MessageEventStore,
        local_directory: DirectoryStore,
        authority_directory: DirectoryStore | None = None,
        delivery_store: DeliveryStore | None = None,
        station_id: str | None = None,
        presence_service: PresenceService | None = None,
    ) -> None:
        self._gateway_registry = gateway_registry
        self._message_events = message_events
        self._local_directory = local_directory
        self._authority_directory = authority_directory
        self._delivery_store = delivery_store
        self._station_id = str(station_id or "").strip() or "local-station"
        if presence_service is None and delivery_store is not None:
            presence_service = PresenceService(
                delivery_store=delivery_store,
                gateway_registry=gateway_registry,
                local_directory=local_directory,
                authority_directory=authority_directory,
                message_events=message_events,
                station_id=self._station_id,
            )
        self._presence_service = presence_service

    def ingest_inbound_text_message(self, message: InboundTextMessage) -> InboundProcessingResult:
        classification = classify_inbound_text_message(message)
        sender_entry: LocalUserEntry | None = None
        target_entry: LocalUserEntry | None = None
        sender_resolution = "ignored"

        if classification.is_valid_ehn and classification.action in {"route", "register"}:
            sender_entry, sender_resolution = self._resolve_sender(
                classification,
                allow_provisional=classification.action == "route",
                message=message,
            )
            if sender_entry is not None:
                self._record_presence(classification, sender_entry)

        if classification.is_valid_ehn and classification.action == "route":
            target_entry = self._resolve_target(classification.target)

        self._message_events.record(
            MessageEvent(
                event_type="inbound_message",
                direction="ingress",
                timestamp=utc_now_iso(),
                gateway_id=classification.gateway_id,
                status=classification.reason or classification.action,
                summary=classification.body[:120],
                network=classification.network,
                sender_endpoint=classification.sender_endpoint,
                sender_user_id=sender_entry.user.user_id if sender_entry is not None else None,
                conversation_scope=classification.conversation_scope,
                target_id=classification.target.target_id if classification.target else None,
                actor_class=classification.actor_class,
                content_class=classification.content_class,
            )
        )

        return InboundProcessingResult(
            classification=classification,
            sender_entry=sender_entry,
            target_entry=target_entry,
            sender_resolution=sender_resolution,
        )

    def ingest_gateway_inbound_events(
        self,
        *,
        gateway_id: str | None = None,
        network: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[InboundProcessingResult]:
        gateway = None
        if gateway_id:
            gateway = self._gateway_registry.get_gateway(gateway_id)
        elif network:
            gateway = self._gateway_registry.find_provider_for_network(network)
        if gateway is None:
            return []

        results: list[InboundProcessingResult] = []
        for event in gateway.list_inbound_events(limit=limit, cursor=cursor):
            normalized = self._normalize_gateway_event(gateway.gateway_id, event, network=network)
            if normalized is None:
                continue
            results.append(self.ingest_inbound_text_message(normalized))
        return results

    def _resolve_sender(
        self,
        classification: ClassifiedInboundMessage,
        *,
        allow_provisional: bool,
        message: InboundTextMessage | None = None,
    ) -> tuple[LocalUserEntry | None, str]:
        endpoint_network = _canonical_endpoint_network(classification.network)
        existing = self._local_directory.find_user_by_endpoint(
            network=endpoint_network,
            native_address=classification.sender_endpoint,
        )
        if existing is not None:
            return existing, "local"

        authority = self._authority_directory
        if authority is not None and authority is not self._local_directory:
            authority_entry = authority.find_user_by_endpoint(
                network=endpoint_network,
                native_address=classification.sender_endpoint,
            )
            if authority_entry is not None:
                copied = self._local_directory.save_directory_user(authority_entry.as_dict())
                return copied, "authority_copy"

        if _supports_rcs_name_lookup(classification.network):
            local_name_match = self._local_directory.find_user_by_rcs_name(classification.sender_endpoint)
            if local_name_match is not None:
                return local_name_match, "local_rcs_name"
            if authority is not None and authority is not self._local_directory:
                authority_name_match = authority.find_user_by_rcs_name(classification.sender_endpoint)
                if authority_name_match is not None:
                    copied = self._local_directory.save_directory_user(authority_name_match.as_dict())
                    return copied, "authority_rcs_name_copy"

        if not allow_provisional:
            return None, "unknown_user"

        observed_name = str(getattr(message, "sender_display_name", None) or "").strip()
        provisional = self._local_directory.create_provisional_sender(
            display_name=observed_name or f"Unknown {classification.network.upper()} sender",
            network=endpoint_network,
            native_address=classification.sender_endpoint,
            actor_class=classification.actor_class,
            messaging_mode="GROUP" if classification.conversation_scope == "group" else "DIRECT",
            capability_tags=self._build_capability_tags(classification),
            gateway_id=classification.gateway_id,
            notes=f"First observed via {classification.network} inbound message.",
        )
        return provisional, "provisional"

    def _resolve_target(self, target: MessageTarget | None) -> LocalUserEntry | None:
        if target is None or target.target_type != "user":
            return None
        local_entry = self._local_directory.get_user_by_id(target.target_id)
        if local_entry is not None:
            return local_entry
        local_identity_entry = self._local_directory.find_user_by_identity_display_name(target.target_id)
        if local_identity_entry is not None:
            return local_identity_entry

        authority = self._authority_directory
        if authority is not None and authority is not self._local_directory:
            authority_entry = authority.get_user_by_id(target.target_id)
            if authority_entry is not None:
                return self._local_directory.save_directory_user(authority_entry.as_dict())
            authority_identity_entry = authority.find_user_by_identity_display_name(target.target_id)
            if authority_identity_entry is not None:
                return self._local_directory.save_directory_user(authority_identity_entry.as_dict())

        return None

    def _normalize_gateway_event(
        self,
        gateway_id: str,
        event: dict[str, Any],
        *,
        network: str | None,
    ) -> InboundTextMessage | None:
        if not isinstance(event, dict):
            return None
        text = str(event.get("text") or event.get("message_text") or event.get("body") or "").strip()
        sender_endpoint = str(
            event.get("sender_public_key")
            or event.get("public_key")
            or event.get("source_endpoint")
            or event.get("from")
            or event.get("phone_number")
            or ""
        ).strip()
        if not text or not sender_endpoint:
            return None
        event_network = str(network or event.get("network") or "meshcore").strip().lower() or "meshcore"
        message_class = str(event.get("message_class") or event.get("kind") or "").strip().lower()
        channel_name = str(event.get("channel_name") or event.get("channel") or "").strip() or None
        is_group = message_class in {"channel_message", "channel", "group_message", "group"}
        return InboundTextMessage(
            gateway_id=gateway_id,
            network=event_network,
            sender_endpoint=sender_endpoint,
            text=text,
            actor_class=str(event.get("actor_class") or "person"),
            content_class=str(event.get("content_class") or ("telemetry" if message_class == "telemetry" else "message")),
            is_plaintext=event.get("is_plaintext") not in {False, "false", 0, "0"},
            is_ehn_group_channel=is_group,
            channel_name=channel_name,
        )

    def _build_capability_tags(self, classification: ClassifiedInboundMessage) -> str:
        tags = [classification.conversation_scope or "unknown", classification.content_class]
        unique_tags = [tag for tag in tags if tag]
        return ",".join(unique_tags)

    def _record_presence(
        self,
        classification: ClassifiedInboundMessage,
        sender_entry: LocalUserEntry,
    ) -> None:
        if self._presence_service is None:
            return
        evidence = "register" if classification.action == "register" else "message"
        self._presence_service.observe_user(
            user_entry=sender_entry,
            network=classification.network,
            evidence=evidence,
            endpoint_address=str(classification.sender_endpoint or "").strip(),
            presence_state="registered" if evidence == "register" else "messaging",
        )