from __future__ import annotations

from dataclasses import dataclass
import json
import mimetypes
import os
import platform
import sqlite3
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ehn_basestation.app.bootstrap import BasestationApp, create_app
from ehn_basestation.app.settings import BasestationSettings, resolve_project_root
from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest
from ehn_basestation.core.events import MessageEvent, utc_now_iso
from ehn_basestation.core.maintenance import MaintenanceScheduler
from ehn_basestation.core.message_policy import InboundTextMessage
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore, LocalUserEntry
from ehn_basestation.gateways.meshcore import MeshCoreGatewayAdapter, MeshCoreRuntimeClient
from ehn_basestation.gateways.sms import AndroidSmsGatewayAdapter


STATIC_DIR = resolve_project_root() / "ui" / "static"


def iso_utc(timestamp: float | None = None) -> str:
    if timestamp is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()


def path_metadata(path: str) -> dict[str, Any]:
    return {
        "path": path,
        "exists": True,
        "size_bytes": 0,
        "modified_utc": iso_utc(),
    }


def _runtime_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("runtime_status") if isinstance(snapshot.get("runtime_status"), dict) else {})


def build_runtime_payload(settings: BasestationSettings, client: MeshCoreRuntimeClient) -> dict[str, Any]:
    snapshot = client.get_snapshot()
    runtime_status = _runtime_status(snapshot)
    connected = bool(snapshot.get("connected"))
    status_text = str(snapshot.get("status") or ("connected" if connected else "unreachable"))
    carrier = str(runtime_status.get("link_carrier") or settings.meshcore_link_carrier or "http")
    endpoint = str(runtime_status.get("link_endpoint") or settings.meshcore_link_endpoint or settings.meshcore_base_url or "")
    return {
        "host": {
            "hostname": settings.station_name,
            "platform": platform.platform(),
            "polled_at_utc": iso_utc(),
        },
        "interface_mode": str(runtime_status.get("interface_mode") or "gateway-link"),
        "service": {
            "Id": settings.meshcore_gateway_id,
            "ActiveState": "active" if connected else "inactive",
            "SubState": status_text,
            "MainPID": None,
            "ExecMainStartTimestamp": None,
            "active": connected,
        },
        "storage_root": str(settings.instance_root),
        "status": {
            **path_metadata(f"{carrier}:{endpoint}" if endpoint else carrier),
            "raw": runtime_status,
            "parsed": {
                **runtime_status,
                "bridge_ready": connected,
                "transport_enabled": connected,
            },
        },
    }


def _safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_from_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _seconds_since_iso(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - observed).total_seconds())


def _derive_sms_phone_link_state(status: dict[str, Any]) -> str:
    seconds = _seconds_since_iso(status.get("last_android_seen_at"))
    if seconds is None:
        return "never_seen"
    if seconds <= 90:
        return "connected"
    if seconds <= 300:
        return "stale"
    return "offline"


def _derive_sms_gateway_status(status: dict[str, Any], *, phone_link_state: str) -> str:
    queued = int(status.get("queued_outbound") or 0)
    claimed = int(status.get("claimed_outbound") or 0)
    failed = int(status.get("failed_outbound") or 0)
    if phone_link_state == "never_seen":
        return "waiting_for_phone"
    if phone_link_state in {"stale", "offline"} and queued > 0:
        return "queue_waiting_for_phone"
    if phone_link_state in {"stale", "offline"}:
        return "phone_stale"
    if claimed > 0:
        return "phone_sending"
    if queued > 0:
        return "queue_pending"
    if failed > 0:
        return "delivery_failed"
    return "connected_idle"


def _normalize_channel(channel: dict[str, Any]) -> dict[str, Any]:
    index = _safe_int(channel.get("index")) or 0
    name = str(channel.get("name") or f"channel-{index}").strip()
    secret_hex = str(channel.get("secret_hex") or "").strip()
    secret_preview = str(channel.get("secret_preview") or ("system" if index == 0 else ("managed remotely" if secret_hex else "-")))
    is_empty = bool(channel.get("is_empty"))
    if not secret_hex and not name and index != 0:
        is_empty = True
    return {
        "index": index,
        "name": name,
        "secret_hex": secret_hex,
        "secret_preview": secret_preview,
        "is_empty": is_empty,
        "browser_send_allowed": bool(channel.get("browser_send_allowed")),
    }


def _find_preferred_meshcore_endpoint(entry: LocalUserEntry | None) -> dict[str, Any] | None:
    if entry is None:
        return None

    ranked_endpoints = sorted(
        (
            endpoint for endpoint in entry.endpoints
            if str(endpoint.network or "").strip().upper() == "MESHCORE"
            and _bool_from_value(endpoint.enabled)
            and str(endpoint.native_address or "").strip()
        ),
        key=lambda endpoint: int(endpoint.priority),
        reverse=True,
    )
    if not ranked_endpoints:
        return None

    selected = ranked_endpoints[0]
    return {
        "endpoint_id": selected.endpoint_id,
        "network": selected.network,
        "native_address": selected.native_address,
        "public_key": selected.native_address,
        "verified": selected.verified,
        "priority": selected.priority,
        "trust_level": selected.trust_level,
        "capability_tags": selected.capability_tags,
    }


def _preferred_language_for_entry(entry: LocalUserEntry | None) -> str:
    if entry is None or not isinstance(entry.profile, dict):
        return "en"
    personal = entry.profile.get("personal") if isinstance(entry.profile.get("personal"), dict) else {}
    return str(personal.get("preferred_language") or "en").strip() or "en"


def _select_target_delivery_endpoint(
    app: BasestationApp,
    target_entry: LocalUserEntry | None,
    *,
    meshcore_client: MeshCoreRuntimeClient | None,
) -> tuple[str | None, dict[str, Any] | None]:
    if target_entry is None:
        return None, None

    endpoints_by_network: dict[str, list[Any]] = {}
    for endpoint in sorted(target_entry.endpoints, key=lambda item: int(item.priority), reverse=True):
        network = str(endpoint.network or "").strip().lower()
        if network == "rcs":
            network = "sms"
        if not network or not _bool_from_value(endpoint.enabled) or not str(endpoint.native_address or "").strip():
            continue
        endpoints_by_network.setdefault(network, []).append(endpoint)

    policy = app.delivery_store.get_delivery_policy(target_entry.user.user_id)
    presence = app.delivery_store.get_user_presence(target_entry.user.user_id)
    ordered_routes: list[str] = []
    for route in policy.preferred_routes if policy is not None else ():
        normalized_route = str(route).strip().lower()
        if normalized_route and normalized_route not in ordered_routes:
            ordered_routes.append(normalized_route)
    presence_route = str(presence.last_seen_via_network or "").strip().lower() if presence is not None else ""
    if presence_route and presence_route not in ordered_routes:
        ordered_routes.append(presence_route)
    for endpoint in sorted(target_entry.endpoints, key=lambda item: int(item.priority), reverse=True):
        normalized_route = str(endpoint.network or "").strip().lower()
        if normalized_route == "rcs":
            normalized_route = "sms"
        if normalized_route and normalized_route not in ordered_routes:
            ordered_routes.append(normalized_route)

    for route in ordered_routes:
        if route == "meshcore" and meshcore_client is None:
            continue
        if route == "sms" and _get_sms_gateway(app) is None:
            continue
        candidates = endpoints_by_network.get(route) or []
        if not candidates:
            continue
        selected = candidates[0]
        return route, {
            "endpoint_id": selected.endpoint_id,
            "network": selected.network,
            "native_address": selected.native_address,
            "public_key": selected.native_address,
            "phone_number": selected.native_address,
            "verified": selected.verified,
            "priority": selected.priority,
            "trust_level": selected.trust_level,
            "capability_tags": selected.capability_tags,
        }

    fallback_route = ordered_routes[0] if ordered_routes else None
    return fallback_route, None


def _queue_known_target_delivery(
    app: BasestationApp,
    *,
    source_entry: LocalUserEntry | None,
    target_user_id: str,
    network: str | None,
    conversation_scope: str,
    content_class: str,
    text: str,
) -> dict[str, Any]:
    queued = app.delivery_store.enqueue_message(
        sender_user_id=(source_entry.user.user_id if source_entry is not None else "system"),
        target_user_id=str(target_user_id).strip(),
        conversation_scope=str(conversation_scope).strip() or "single",
        message_class=str(content_class).strip() or "message",
        plaintext_body=str(text or ""),
        accepted_station_id=app.settings.station_id,
        owning_station_id=app.settings.station_id,
        selected_route=str(network or "").strip(),
    )
    _record_delivery_event(
        app,
        gateway_id="queued",
        delivery_id=queued.message_id,
        status="accepted_but_queued",
        network=str(network or "queued") or "queued",
        summary=f"Queued message for user {target_user_id}.",
        content_class=content_class,
    )
    return {
        "ok": True,
        "accepted": True,
        "mode": "direct",
        "network": str(network or "queued") or "queued",
        "gateway_id": "queued",
        "delivery_id": queued.message_id,
        "status": "accepted_but_queued",
        "detail": f"No immediate route is currently available for user {target_user_id}; message stored for later delivery.",
        "target_id": target_user_id,
        "transport_message_id": queued.message_id,
    }


def _find_directory_user_by_endpoint(
    app: BasestationApp | None,
    *,
    network: str | None,
    native_address: str | None,
) -> LocalUserEntry | None:
    if app is None:
        return None
    normalized_network = str(network or "").strip().upper()
    if normalized_network.startswith("MESHCORE"):
        normalized_network = "MESHCORE"
    elif normalized_network.startswith("MESHTASTIC"):
        normalized_network = "MESHTASTIC"
    elif normalized_network in {"SMS", "RCS"}:
        normalized_network = "SMS"
    normalized_address = str(native_address or "").strip()
    if not normalized_network or not normalized_address:
        return None

    local_entry = app.directory_store.find_user_by_endpoint(
        network=normalized_network,
        native_address=normalized_address,
    )
    if local_entry is not None:
        return local_entry

    authority_store = app.authority_directory_store
    if authority_store is app.directory_store:
        return None
    return authority_store.find_user_by_endpoint(
        network=normalized_network,
        native_address=normalized_address,
    )


def _find_directory_user_by_id(
    app: BasestationApp | None,
    *,
    user_id: str | None,
) -> LocalUserEntry | None:
    normalized_user_id = str(user_id or "").strip()
    if app is None or not normalized_user_id:
        return None

    local_entry = app.directory_store.get_user_by_id(normalized_user_id)
    if local_entry is not None:
        return local_entry

    authority_store = app.authority_directory_store
    if authority_store is app.directory_store:
        return None
    return authority_store.get_user_by_id(normalized_user_id)


def _resolve_message_source(
    app: BasestationApp | None,
    *,
    network: str | None,
    native_address: str | None,
) -> tuple[str | None, str | None]:
    canonical_identity = str(native_address or "").strip() or None
    if canonical_identity is None:
        return None, None
    entry = _find_directory_user_by_endpoint(
        app,
        network=network,
        native_address=canonical_identity,
    )
    if entry is None:
        return canonical_identity, canonical_identity
    return str(entry.user.user_id).strip() or canonical_identity, canonical_identity


def _display_name_for_user_id(app: BasestationApp | None, user_id: str | None) -> str | None:
    entry = _find_directory_user_by_id(app, user_id=user_id)
    if entry is None:
        return None
    display_name = str(entry.user.display_name or "").strip()
    return display_name or None


def _identity_label_for_entry(entry: LocalUserEntry | None) -> str | None:
    if entry is None:
        return None
    identity_label = str(entry.identity.display_name or "").strip()
    if identity_label:
        return identity_label
    user_id = str(entry.user.user_id or "").strip()
    if user_id:
        return user_id
    display_name = str(entry.user.display_name or "").strip()
    return display_name or None


def _identity_label_for_user_id(app: BasestationApp | None, user_id: str | None) -> str | None:
    entry = _find_directory_user_by_id(app, user_id=user_id)
    return _identity_label_for_entry(entry)


def _identity_label_for_endpoint(
    app: BasestationApp | None,
    *,
    network: str | None,
    native_address: str | None,
) -> str | None:
    entry = _find_directory_user_by_endpoint(
        app,
        network=network,
        native_address=native_address,
    )
    return _identity_label_for_entry(entry)


def _ensure_local_target_entry(
    app: BasestationApp,
    *,
    target_id: str | None,
    target_entry: LocalUserEntry | None,
) -> LocalUserEntry | None:
    normalized_target_id = str(target_id or "").strip()
    if target_entry is not None:
        local_entry = app.directory_store.get_user_by_id(target_entry.user.user_id)
        if local_entry is not None:
            return local_entry
        return app.directory_store.save_directory_user(target_entry.as_dict())
    if not normalized_target_id:
        return None

    local_entry = app.directory_store.get_user_by_id(normalized_target_id)
    if local_entry is not None:
        return local_entry
    local_identity_entry = app.directory_store.find_user_by_identity_display_name(normalized_target_id)
    if local_identity_entry is not None:
        return local_identity_entry

    authority_entry = app.authority_directory_store.get_user_by_id(normalized_target_id)
    if authority_entry is not None:
        return app.directory_store.save_directory_user(authority_entry.as_dict())
    authority_identity_entry = app.authority_directory_store.find_user_by_identity_display_name(normalized_target_id)
    if authority_identity_entry is not None:
        return app.directory_store.save_directory_user(authority_identity_entry.as_dict())
    return None


def _augment_sms_gateway_inbound_event(
    app: BasestationApp | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    augmented = dict(event)
    sender_user_id = str(augmented.get("sender_user_id") or "").strip() or None
    sender_label = _identity_label_for_user_id(app, sender_user_id)
    if sender_label is None:
        sender_label = sender_user_id or str(augmented.get("from_number") or augmented.get("source_endpoint") or "").strip() or None
    augmented["sender_label"] = sender_label
    return augmented


def _select_default_private_meshcore_channel(client: MeshCoreRuntimeClient) -> dict[str, Any] | None:
    private_channels = [
        _normalize_channel(channel)
        for channel in client.list_channels()
        if isinstance(channel, dict)
    ]
    private_channels = [
        channel for channel in private_channels
        if int(channel.get("index") or 0) > 0 and not bool(channel.get("is_empty"))
    ]
    if not private_channels:
        return None
    private_channels.sort(key=lambda channel: int(channel.get("index") or 0))
    return private_channels[0]


def _is_provisional_entry(entry: LocalUserEntry | None) -> bool:
    if entry is None:
        return True
    return str(entry.user.retention_class or "").strip().upper() == "FIELD_OBSERVED"


def _format_sms_sender_label(
    *,
    sender_entry: LocalUserEntry | None,
    from_number: str | None,
    from_name: str | None = None,
) -> str:
    # Known users are shown by canonical label; provisional senders by what the phone saw (RCS name, then number),
    # so a recipient reads "a person or number", not an auto-generated id.
    if not _is_provisional_entry(sender_entry):
        identity_label = _identity_label_for_entry(sender_entry)
        if identity_label:
            return identity_label
    name = str(from_name or "").strip()
    if name:
        return name
    number = str(from_number or "").strip()
    if number:
        return number
    if sender_entry is not None:
        stored_name = str(sender_entry.user.display_name or "").strip()
        if stored_name and not stored_name.lower().startswith("unknown "):
            return stored_name
    return _identity_label_for_entry(sender_entry) or "sms-sender"


def _render_meshcore_sms_bridge_text(*, sender_label: str, body: str) -> str:
    return f"SMS {sender_label}: {str(body or '').strip()}"


def _render_meshcore_private_bridge_text(*, sender_label: str, body: str) -> str:
    return f"MeshCore {sender_label}: {str(body or '').strip()}"


def _meshcore_ingest_state_path(settings: BasestationSettings) -> Path:
    return settings.instance_root / "stations" / "local" / "state" / "meshcore-ingest-state.json"


def _load_meshcore_ingest_state(settings: BasestationSettings) -> dict[str, Any]:
    state_path = _meshcore_ingest_state_path(settings)
    if not state_path.exists():
        return {"processed_private_event_keys": []}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed_private_event_keys": []}
    if not isinstance(payload, dict):
        return {"processed_private_event_keys": []}
    keys = payload.get("processed_private_event_keys")
    if not isinstance(keys, list):
        keys = []
    return {"processed_private_event_keys": [str(item) for item in keys if str(item).strip()]}


def _save_meshcore_ingest_state(settings: BasestationSettings, state: dict[str, Any]) -> None:
    state_path = _meshcore_ingest_state_path(settings)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _meshcore_event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or "").strip().lower()


def _is_routable_meshcore_private_event(event: dict[str, Any]) -> bool:
    event_type = _meshcore_event_type(event)
    if event_type in {"meshcore_private_rx", "contact_message_rx"}:
        return True
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    message_scope = str(payload.get("message_scope") or raw.get("message_scope") or "").strip().lower()
    return message_scope == "direct"


def _meshcore_event_sender_public_key(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    return str(
        sender.get("public_key")
        or raw.get("sender_public_key")
        or event.get("sender_public_key")
        or event.get("public_key")
        or ""
    ).strip()


def _meshcore_event_text(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(payload.get("text") or event.get("text") or event.get("message_text") or event.get("body") or event.get("summary") or "").strip()


def _normalize_meshcore_event_for_routing(
    settings: BasestationSettings,
    event: dict[str, Any],
) -> InboundTextMessage | None:
    sender_public_key = _meshcore_event_sender_public_key(event)
    text = _meshcore_event_text(event)
    if not sender_public_key or not text:
        return None
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    message_scope = str(payload.get("message_scope") or raw.get("message_scope") or "").strip().lower()
    channel_name = str(payload.get("channel_name") or raw.get("channel_name") or event.get("channel_name") or "").strip() or None
    return InboundTextMessage(
        gateway_id=settings.meshcore_gateway_id,
        network="meshcore",
        sender_endpoint=sender_public_key,
        text=text,
        actor_class=str(payload.get("actor_class") or event.get("actor_class") or "person"),
        content_class=str(payload.get("content_class") or event.get("content_class") or "message"),
        is_plaintext=payload.get("is_plaintext") not in {False, "false", 0, "0"},
        is_ehn_group_channel=message_scope == "channel",
        channel_name=channel_name,
    )


def _meshcore_private_event_key(event: dict[str, Any]) -> str | None:
    event_id = str(event.get("event_id") or event.get("id") or "").strip()
    if event_id:
        return f"id:{event_id}"
    sender_public_key = _meshcore_event_sender_public_key(event)
    text = _meshcore_event_text(event)
    event_ts = str(event.get("event_ts_utc") or event.get("timestamp") or "").strip()
    if not sender_public_key or not text or not event_ts:
        return None
    return f"fp:{event_ts}|{sender_public_key}|{text}"


def _unknown_target_payload(app: BasestationApp, *, target_id: str, content_class: str) -> dict[str, Any]:
    _record_delivery_event(
        app,
        gateway_id="unresolved",
        delivery_id=f"unknown-target-{utc_now_iso()}",
        status="unknown_target",
        network="none",
        summary=f"Direct target {target_id} is not a known user; message not stored.",
        content_class=content_class,
    )
    return {
        "ok": False,
        "accepted": False,
        "mode": "direct",
        "network": None,
        "status": "unknown_target",
        "detail": f"User {target_id} is not known to this station or the authority directory.",
        "target_id": target_id,
    }


def route_inbound_sink_submission_payload(
    app: BasestationApp,
    *,
    routing_result: Any,
) -> dict[str, Any] | None:
    classification = routing_result.classification
    if classification.action != "route" or not classification.is_valid_ehn:
        return None
    target = classification.target
    if target is None or target.target_type != "sink":
        return None
    sender_entry = routing_result.sender_entry
    result = app.sink_delivery.submit(
        sink_id=target.target_id,
        body=classification.body,
        sender_user_id=sender_entry.user.user_id if sender_entry is not None else None,
        actor_class=classification.actor_class,
        content_class=classification.content_class,
        source_network=classification.network,
        source_gateway_id=classification.gateway_id,
    )
    return result.as_dict()


def route_inbound_meshcore_private_to_user_payload(
    app: BasestationApp,
    settings: BasestationSettings,
    client: MeshCoreRuntimeClient,
    *,
    routing_result: Any,
) -> dict[str, Any] | None:
    classification = routing_result.classification
    if classification.action != "route" or not classification.is_valid_ehn:
        return None
    target = classification.target
    if target is not None and target.target_type == "sink":
        return route_inbound_sink_submission_payload(app, routing_result=routing_result)
    if target is None or target.target_type != "user":
        return None

    sender_label = _identity_label_for_entry(routing_result.sender_entry) or classification.sender_endpoint
    rendered_text = _render_meshcore_private_bridge_text(sender_label=sender_label, body=classification.body)
    delivery_id = f"meshcore-ingress-{utc_now_iso()}"
    target_entry = _ensure_local_target_entry(
        app,
        target_id=target.target_id,
        target_entry=routing_result.target_entry,
    )
    if target_entry is None:
        return _unknown_target_payload(app, target_id=target.target_id, content_class=classification.content_class)
    selected_network, endpoint = _select_target_delivery_endpoint(
        app,
        target_entry,
        meshcore_client=client,
    )
    if endpoint is None:
        return _queue_known_target_delivery(
            app,
            source_entry=routing_result.sender_entry,
            target_user_id=target.target_id,
            network=selected_network,
            conversation_scope=classification.conversation_scope,
            content_class=classification.content_class,
            text=rendered_text,
        )

    result = app.delivery_orchestrator.send_directed_private(
        network=selected_network or "meshcore",
        request=DirectedPrivateDeliveryRequest(
            delivery_id=delivery_id,
            destination=endpoint,
            payload={
                "text": rendered_text,
                "content_class": classification.content_class,
                "ham_plaintext": rendered_text,
            },
            source_identity={
                "network": classification.network,
                "sender_endpoint": classification.sender_endpoint,
                "display_name": sender_label,
            },
            metadata={
                "source_network": classification.network,
                "source_gateway_id": classification.gateway_id,
                "source_sender_endpoint": classification.sender_endpoint,
                "source_user_id": routing_result.sender_entry.user.user_id if routing_result.sender_entry is not None else None,
                "source_display_name": routing_result.sender_entry.user.display_name if routing_result.sender_entry is not None else None,
                "conversation_scope": classification.conversation_scope,
                "target_identity_label": _identity_label_for_entry(target_entry) if target_entry is not None else target.target_id,
                "target_user_id": target.target_id,
                "target_id": target.target_id,
            },
        ),
    )
    payload = _delivery_result_payload(result, mode="direct")
    payload["target_id"] = target.target_id
    return payload


def _process_meshcore_private_inbound_events(
    settings: BasestationSettings,
    client: MeshCoreRuntimeClient,
    app: BasestationApp | None,
    *,
    events: list[dict[str, Any]],
) -> None:
    if app is None:
        return

    state = _load_meshcore_ingest_state(settings)
    processed_private_event_keys = {
        str(item).strip()
        for item in state.get("processed_private_event_keys", [])
        if str(item).strip()
    }
    updated = False

    for event in events:
        if not isinstance(event, dict):
            continue
        if not _is_routable_meshcore_private_event(event):
            continue
        event_key = _meshcore_private_event_key(event)
        if not event_key or event_key in processed_private_event_keys:
            continue

        normalized = _normalize_meshcore_event_for_routing(settings, event)
        if normalized is None:
            continue
        routing_result = app.inbound_router.ingest_inbound_text_message(normalized)
        route_inbound_meshcore_private_to_user_payload(
            app,
            settings,
            client,
            routing_result=routing_result,
        )
        processed_private_event_keys.add(event_key)
        updated = True

    if updated:
        ordered_keys = list(processed_private_event_keys)
        ordered_keys.sort()
        _save_meshcore_ingest_state(
            settings,
            {"processed_private_event_keys": ordered_keys[-200:]},
        )


def _process_meshcore_private_inbound_event_for_display(
    event: dict[str, Any],
    app: BasestationApp | None,
) -> None:
    if app is None:
        return
    if not _is_routable_meshcore_private_event(event):
        return

    settings = app.settings
    state = _load_meshcore_ingest_state(settings)
    processed_private_event_keys = {
        str(item).strip()
        for item in state.get("processed_private_event_keys", [])
        if str(item).strip()
    }
    event_key = _meshcore_private_event_key(event)
    if not event_key or event_key in processed_private_event_keys:
        return

    normalized = _normalize_meshcore_event_for_routing(settings, event)
    if normalized is None:
        return
    routing_result = app.inbound_router.ingest_inbound_text_message(normalized)
    route_inbound_meshcore_private_to_user_payload(
        app,
        settings,
        MeshCoreRuntimeClient.from_settings(settings),
        routing_result=routing_result,
    )
    processed_private_event_keys.add(event_key)
    ordered_keys = list(processed_private_event_keys)
    ordered_keys.sort()
    _save_meshcore_ingest_state(
        settings,
        {"processed_private_event_keys": ordered_keys[-200:]},
    )


def _record_delivery_event(
    app: BasestationApp,
    *,
    gateway_id: str,
    delivery_id: str,
    status: str,
    network: str,
    summary: str,
    content_class: str = "message",
) -> None:
    app.message_events.record(
        MessageEvent(
            event_type="delivery_dispatch",
            direction="delivery",
            timestamp=utc_now_iso(),
            gateway_id=gateway_id,
            delivery_id=delivery_id,
            status=status,
            summary=summary,
            network=network,
            content_class=content_class,
        )
    )


def _delivery_result_payload(result: DeliveryResult, *, mode: str) -> dict[str, Any]:
    return {
        "ok": result.accepted,
        "accepted": result.accepted,
        "mode": mode,
        "network": result.network,
        "gateway_id": result.gateway_id,
        "delivery_id": result.delivery_id,
        "status": result.status,
        "detail": result.detail,
        "transport_message_id": result.transport_message_id,
    }


def route_inbound_sms_to_meshcore_payload(
    app: BasestationApp,
    settings: BasestationSettings,
    client: MeshCoreRuntimeClient | None,
    *,
    from_number: str | None,
    from_name: str | None,
    body: str,
    routing_result: Any,
) -> dict[str, Any] | None:
    classification = routing_result.classification
    if classification.action != "route" or not classification.is_valid_ehn:
        return None
    target = classification.target
    if target is None:
        return None

    sender_label = _format_sms_sender_label(
        sender_entry=routing_result.sender_entry,
        from_number=from_number,
        from_name=from_name,
    )
    rendered_text = _render_meshcore_sms_bridge_text(sender_label=sender_label, body=body)
    delivery_id = f"sms-mesh-{utc_now_iso()}"

    if target.target_type == "user":
        target_entry = _ensure_local_target_entry(
            app,
            target_id=target.target_id,
            target_entry=routing_result.target_entry,
        )
        if target_entry is None:
            return _unknown_target_payload(app, target_id=target.target_id, content_class=classification.content_class)
        selected_network, endpoint = _select_target_delivery_endpoint(
            app,
            target_entry,
            meshcore_client=client,
        )
        if endpoint is None:
            return _queue_known_target_delivery(
                app,
                source_entry=routing_result.sender_entry,
                target_user_id=target.target_id,
                network=selected_network,
                conversation_scope=classification.conversation_scope,
                content_class=classification.content_class,
                text=rendered_text,
            )

        result = app.delivery_orchestrator.send_directed_private(
            network=selected_network or "meshcore",
            request=DirectedPrivateDeliveryRequest(
                delivery_id=delivery_id,
                destination=endpoint,
                payload={
                    "text": rendered_text,
                    "content_class": classification.content_class,
                    "ham_plaintext": rendered_text,
                },
                source_identity={
                    "network": classification.network,
                    "sender_endpoint": classification.sender_endpoint,
                    "display_name": sender_label,
                },
                metadata={
                    "source_network": classification.network,
                    "source_gateway_id": classification.gateway_id,
                    "source_sender_endpoint": classification.sender_endpoint,
                    "source_user_id": routing_result.sender_entry.user.user_id if routing_result.sender_entry is not None else None,
                    "source_display_name": routing_result.sender_entry.user.display_name if routing_result.sender_entry is not None else None,
                    "conversation_scope": classification.conversation_scope,
                    "target_user_id": target.target_id,
                    "target_id": target.target_id,
                },
            ),
        )
        payload = _delivery_result_payload(result, mode="direct")
        payload["target_id"] = target.target_id
        return payload

    if target.target_type == "sink":
        return route_inbound_sink_submission_payload(app, routing_result=routing_result)

    if target.target_type == "group":
        local_group_aliases = {
            str(settings.sms_gateway_id or "").strip().upper(),
            str(settings.meshcore_gateway_id or "").strip().upper(),
            str(settings.station_name or "").strip().upper(),
        }
        if str(target.target_id or "").strip().upper() not in local_group_aliases:
            return {
                "ok": False,
                "accepted": False,
                "mode": "group",
                "network": "meshcore",
                "status": "non_local_group",
                "detail": f"Group target {target.target_id} is not local to this station.",
                "target_id": target.target_id,
            }

        channel = _select_default_private_meshcore_channel(client)
        if channel is None:
            return {
                "ok": False,
                "accepted": False,
                "mode": "group",
                "network": "meshcore",
                "status": "missing_group_channel",
                "detail": "No active private MeshCore channel is available for group delivery.",
                "target_id": target.target_id,
            }

        accepted, message, payload = client.send_channel_message(
            channel_index=int(channel.get("index") or 0),
            channel_name=str(channel.get("name") or "").strip() or None,
            text=rendered_text,
            delivery_id=delivery_id,
        )
        result_status = "accepted" if accepted else "rejected"
        _record_delivery_event(
            app,
            gateway_id=settings.meshcore_gateway_id,
            delivery_id=delivery_id,
            status=result_status,
            network="meshcore",
            summary=str(message or rendered_text),
            content_class=classification.content_class,
        )
        return {
            "ok": accepted,
            "accepted": accepted,
            "mode": "group",
            "network": "meshcore",
            "gateway_id": settings.meshcore_gateway_id,
            "delivery_id": delivery_id,
            "status": result_status,
            "detail": message,
            "target_id": target.target_id,
            "channel_index": int(channel.get("index") or 0),
            "channel_name": channel.get("name"),
            "transport_payload": payload,
        }

    return None


def build_node_payload(settings: BasestationSettings, client: MeshCoreRuntimeClient) -> dict[str, Any]:
    snapshot = client.get_snapshot()
    identity = dict(snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {})
    runtime_status = _runtime_status(snapshot)
    channels = [_normalize_channel(channel) for channel in client.list_channels() if isinstance(channel, dict)]
    gps_enabled_raw = identity.get("gps_enabled")
    gps_interval = _safe_int(identity.get("gps_interval_s") or identity.get("gps_interval"))
    gps_mode = str(identity.get("gps_mode") or "").strip() or None
    gps_supported_raw = identity.get("gps_supported")
    gps_enabled = bool(gps_enabled_raw) if gps_enabled_raw is not None else None
    gps_supported = bool(gps_supported_raw) if gps_supported_raw is not None else None
    # A bare interval pref is not evidence of GPS hardware; only mode/supported/enabled fields are.
    if gps_mode is None and gps_supported is None and gps_enabled is None:
        gps_mode = "not_supported"
        gps_supported = False
    return {
        "prefs_path": "gateway-link://node-snapshot",
        "prefs_meta": path_metadata("gateway-link://node-snapshot"),
        "channels_path": "gateway-link://channels",
        "channels_meta": path_metadata("gateway-link://channels"),
        "prefs": {
            "node_name": str(identity.get("display_name") or identity.get("short_name") or snapshot.get("display_name") or "meshcore-node").strip(),
            "lat": _safe_float(identity.get("lat") or identity.get("latitude")),
            "lon": _safe_float(identity.get("lon") or identity.get("longitude")),
            "freq_mhz": _safe_float(runtime_status.get("freq_mhz") or identity.get("freq_mhz")),
            "sf": _safe_int(runtime_status.get("sf") or identity.get("sf")),
            "cr": _safe_int(runtime_status.get("cr") or identity.get("cr")),
            "bw_khz": _safe_float(runtime_status.get("bw_khz") or identity.get("bw_khz")),
            "tx_power_dbm": _safe_int(runtime_status.get("tx_power_dbm") or identity.get("tx_power_dbm")),
            "tx_power_max_dbm": _safe_int(runtime_status.get("tx_power_max_dbm") or identity.get("tx_power_max_dbm")),
            "telemetry_mode_base": _safe_int(identity.get("telemetry_mode_base")),
            "telemetry_mode_loc": _safe_int(identity.get("telemetry_mode_loc")),
            "telemetry_mode_env": _safe_int(identity.get("telemetry_mode_env")),
            "advert_loc_policy": _safe_int(identity.get("advert_loc_policy")),
            "path_hash_mode": _safe_int(runtime_status.get("path_hash_mode") or identity.get("path_hash_mode")),
            "ble_pin": _safe_int(identity.get("ble_pin")),
            "gps_enabled": gps_enabled,
            "gps_interval_s": gps_interval,
            "gps_mode": gps_mode,
            "gps_supported": gps_supported,
            "default_scope_name": str(identity.get("default_scope_name") or runtime_status.get("scope_name") or settings.meshcore_gateway_id).strip(),
            "autoadd_config": _safe_int(identity.get("autoadd_config")),
        },
        "channel_count": len(channels),
        "channels": channels,
    }


def _short_key(value: str) -> str:
    if len(value) <= 16:
        return value
    return f"{value[:8]}...{value[-8:]}"


def build_contacts_payload(client: MeshCoreRuntimeClient) -> dict[str, Any]:
    observed_nodes = client.list_observed_nodes()
    contacts = []
    for node in observed_nodes:
        if not isinstance(node, dict):
            continue
        pub_key = str(node.get("public_key") or node.get("node_id") or node.get("native_address") or "unknown")
        contacts.append(
            {
                "pub_key": pub_key,
                "pub_key_short": _short_key(pub_key),
                "name": str(node.get("contact_name") or node.get("display_name") or node.get("short_name") or node.get("node_id") or "observed-node"),
                "type": node.get("type") or node.get("node_type") or "observed",
                "flags": node.get("flags") or "",
                "sync_since": None,
                "out_path_len": _safe_int(node.get("path_len") or node.get("hops")),
                "last_advert_timestamp": None,
                "last_advert_utc": node.get("last_advert_utc") or node.get("last_seen_at") or node.get("observed_at"),
                "lastmod": None,
                "lastmod_utc": node.get("last_seen_at") or node.get("observed_at"),
                "gps_lat": _safe_float(node.get("gps_lat") or node.get("latitude")),
                "gps_lon": _safe_float(node.get("gps_lon") or node.get("longitude")),
            }
        )
    contacts.sort(key=lambda item: str(item.get("lastmod_utc") or item.get("last_advert_utc") or ""), reverse=True)
    return {
        "contacts_path": "gateway-link://observed-nodes",
        "contacts_meta": path_metadata("gateway-link://observed-nodes"),
        "contact_count": len(contacts),
        "recent_contacts": contacts[:8],
    }


def _normalize_inbound_event(event: dict[str, Any], app: BasestationApp | None = None) -> dict[str, Any]:
    if app is not None and str(event.get("network") or event.get("transport") or "meshcore").strip().lower() == "meshcore":
        _process_meshcore_private_inbound_event_for_display(event, app)
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    network = str(event.get("network") or event.get("transport") or "meshcore")
    source_identity = str(
        sender.get("public_key")
        or raw.get("sender_public_key")
        or raw.get("source_endpoint")
        or ""
    ).strip() or None
    source_id, resolved_identity = _resolve_message_source(
        app,
        network=network,
        native_address=source_identity,
    )
    source_label = _identity_label_for_user_id(app, source_id)
    return {
        "direction": "received",
        "channel_index": raw.get("channel_index", payload.get("channel_index", 0)),
        "text": payload.get("text") or event.get("summary") or "",
        "detail": f"{event.get('transport', 'meshcore')} • {event.get('event_type', event.get('network', 'event'))}",
        "event_utc": event.get("event_ts_utc") or payload.get("received_at") or payload.get("event_utc") or iso_utc(),
        "activity_state": "messaging",
        "message_scope": payload.get("message_scope") or raw.get("message_scope"),
        "recipient_prefix": source_id,
        "source_id": source_id,
        "source_label": source_label or source_id,
        "source_identity": resolved_identity,
        "target_id": None,
        "network": network,
    }


def _normalize_outbound_event(item: dict[str, Any], app: BasestationApp | None = None) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    destination = item.get("destination") if isinstance(item.get("destination"), dict) else {}
    destination_identity = item.get("destination_identity") if isinstance(item.get("destination_identity"), dict) else {}
    reply_context = item.get("reply_context") if isinstance(item.get("reply_context"), dict) else {}
    response = item.get("response") if isinstance(item.get("response"), dict) else {}
    message_class = str(item.get("message_class") or "outbound").strip() or "outbound"
    state = str(item.get("state") or "queued").strip() or "queued"
    state_label = "accepted by gateway" if bool(item.get("accepted")) and message_class == "channel" and response else state
    raw_target_id = str(
        destination.get("channel_name")
        or destination.get("native_address")
        or destination.get("public_key")
        or f"channel-{destination.get('channel_index')}"
    ).strip() or None
    target_user_id = str(
        reply_context.get("target_user_id")
        or item.get("target_user_id")
        or destination_identity.get("user_id")
        or ""
    ).strip() or None
    target_id = target_user_id or raw_target_id
    target_label = str(
        reply_context.get("target_identity_label")
        or destination_identity.get("display_name")
        or destination_identity.get("identity_display_name")
        or ""
    ).strip() or None
    if target_user_id is not None:
        target_label = _identity_label_for_user_id(app, target_user_id) or target_label or target_user_id
    if app is not None and message_class == "directed_private":
        destination_address = str(
            destination.get("native_address")
            or destination.get("public_key")
            or ""
        ).strip()
        destination_entry = _find_directory_user_by_endpoint(
            app,
            network=item.get("network") or "meshcore",
            native_address=destination_address,
        )
        if destination_entry is not None:
            target_id = str(destination_entry.user.user_id or "").strip() or target_id or raw_target_id
            target_label = _identity_label_for_entry(destination_entry) or target_label or target_id
    return {
        "direction": "sent",
        "channel_index": destination.get("channel_index", 0),
        "text": payload.get("text") or item.get("summary") or "",
        "detail": f"{item.get('network', 'meshcore')} • {message_class} • {state_label}",
        "event_utc": item.get("updated_at") or item.get("requested_at") or iso_utc(),
        "message_scope": message_class,
        "recipient_prefix": target_id,
        "source_id": target_id,
        "source_label": target_label or target_id,
        "source_identity": raw_target_id,
        "target_id": target_id,
        "target_label": target_label or target_id,
        "network": str(item.get("network") or "meshcore"),
    }


def _normalize_basestation_event(event: dict[str, Any], app: BasestationApp | None = None) -> dict[str, Any]:
    direction = "received" if str(event.get("direction") or "").lower() == "ingress" else "sent"
    status = str(event.get("status") or "").strip().lower()
    sender_user_id = str(event.get("sender_user_id") or "").strip() or None
    sender = str(event.get("sender_endpoint") or "").strip() or None
    target = str(event.get("target_id") or "").strip() or None
    summary = str(event.get("summary") or "").strip()
    detail_parts = [
        str(event.get("network") or "basestation"),
        str(event.get("status") or event.get("event_type") or "event"),
    ]
    if target and target != sender_user_id:
        detail_parts.append(f"to {target}")
    if event.get("content_class"):
        detail_parts.append(str(event.get("content_class")))
    if sender_user_id is None and sender is not None:
        # Log-only events skip router sender resolution, so map the endpoint to a known user_id here for display.
        resolved_user_id, _ = _resolve_message_source(app, network=event.get("network"), native_address=sender)
        if resolved_user_id and resolved_user_id != sender:
            sender_user_id = resolved_user_id
    source_id = sender_user_id or sender or target
    return {
        "direction": direction,
        "channel_index": -1,
        "text": summary,
        "detail": " • ".join(part for part in detail_parts if part),
        "event_utc": event.get("timestamp") or iso_utc(),
        "activity_state": "registered" if status == "register" else ("messaging" if direction == "received" else None),
        "message_scope": event.get("conversation_scope") or event.get("event_type"),
        "recipient_prefix": source_id,
        "source_id": source_id,
        "source_label": _identity_label_for_user_id(app, sender_user_id) or source_id,
        "source_identity": sender,
        "source_display_name": _display_name_for_user_id(app, sender_user_id),
        "target_id": target,
        "network": str(event.get("network") or "basestation"),
    }


def _normalize_android_sms_outbound(item: dict[str, Any]) -> dict[str, Any]:
    target_id = str(item.get("to_number") or "").strip() or None
    return {
        "direction": "sent",
        "channel_index": -1,
        "text": item.get("body") or item.get("detail") or "",
        "detail": f"sms • {item.get('state', 'queued')}",
        "event_utc": item.get("updated_at") or item.get("claimed_at") or item.get("requested_at") or iso_utc(),
        "message_scope": "single",
        "recipient_prefix": target_id,
        "source_id": target_id,
        "source_identity": target_id,
        "target_id": target_id,
        "network": "sms",
    }


def _active_user_key(entry: dict[str, Any]) -> str:
    return str(entry.get("id") or entry.get("label") or entry.get("network") or "unknown")


def _merge_registered_user(
    users_by_key: dict[str, dict[str, Any]],
    *,
    key: str,
    user_id: str | None,
    label: str | None,
    network: str | None,
    state: str,
    last_heard_utc: str | None,
    detail: str | None,
) -> None:
    normalized_key = str(key or "").strip()
    normalized_label = str(label or "").strip()
    if not normalized_key or not normalized_label:
        return
    normalized_state = "messaging" if str(state or "").strip().lower() == "messaging" else "registered"
    existing = users_by_key.get(normalized_key)
    if existing is None:
        users_by_key[normalized_key] = {
            "id": normalized_key,
            "user_id": str(user_id or "").strip() or None,
            "label": normalized_label,
            "network": str(network or "mixed").strip() or "mixed",
            "state": normalized_state,
            "detail": str(detail or normalized_state).strip() or normalized_state,
            "last_heard_utc": str(last_heard_utc or "").strip() or None,
            "last_event_utc": str(last_heard_utc or "").strip() or None,
        }
        return
    if normalized_state == "messaging":
        existing["state"] = "messaging"
    if last_heard_utc and str(last_heard_utc) > str(existing.get("last_heard_utc") or ""):
        existing["last_heard_utc"] = last_heard_utc
        existing["last_event_utc"] = last_heard_utc
        if network:
            existing["network"] = str(network)
        if detail:
            existing["detail"] = str(detail)


def _build_registered_users(
    contacts: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    sms_gateway: AndroidSmsGatewayAdapter | None,
    *,
    app: BasestationApp | None,
    limit: int,
) -> list[dict[str, Any]]:
    users_by_key: dict[str, dict[str, Any]] = {}

    raw_events = app.message_events.list_events() if app is not None else []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        sender_user_id = str(event.get("sender_user_id") or "").strip()
        if not sender_user_id:
            continue
        label = _identity_label_for_user_id(app, sender_user_id)
        if not label:
            continue
        status = str(event.get("status") or "").strip().lower()
        state = "registered" if status == "register" else "messaging"
        detail = state
        _merge_registered_user(
            users_by_key,
            key=f"user:{sender_user_id}",
            user_id=sender_user_id,
            label=label,
            network=str(event.get("network") or "mixed"),
            state=state,
            last_heard_utc=str(event.get("timestamp") or "").strip() or None,
            detail=detail,
        )

    for message in messages:
        if str(message.get("direction") or "").strip().lower() != "received":
            continue
        activity_state = str(message.get("activity_state") or "").strip().lower()
        source_id = str(message.get("source_id") or "").strip()
        source_label = str(message.get("source_label") or "").strip()
        resolved_label = _identity_label_for_user_id(app, source_id)
        if not source_id or not source_label or not resolved_label or source_label != resolved_label or activity_state not in {"registered", "messaging"}:
            continue
        _merge_registered_user(
            users_by_key,
            key=f"user:{source_id}",
            user_id=source_id,
            label=source_label,
            network=str(message.get("network") or "mixed"),
            state=activity_state,
            last_heard_utc=str(message.get("event_utc") or "").strip() or None,
            detail=activity_state,
        )

    if sms_gateway is not None:
        for event in sms_gateway.list_inbound_events(limit=limit):
            sender_user_id = str(event.get("sender_user_id") or "").strip()
            label = str(event.get("sender_label") or "").strip()
            if not sender_user_id or not label:
                continue
            if f"user:{sender_user_id}" in users_by_key:
                continue
            _merge_registered_user(
                users_by_key,
                key=f"user:{sender_user_id}",
                user_id=sender_user_id,
                label=label,
                network=str(event.get("network") or "sms"),
                state="registered",
                last_heard_utc=event.get("observed_at_utc") or event.get("event_ts_utc"),
                detail="registered",
            )

    for contact in contacts:
        native_address = str(contact.get("pub_key") or "").strip()
        entry = _find_directory_user_by_endpoint(
            app,
            network="meshcore",
            native_address=native_address,
        )
        label = _identity_label_for_entry(entry)
        if entry is None or not label:
            continue
        _merge_registered_user(
            users_by_key,
            key=f"user:{entry.user.user_id}",
            user_id=entry.user.user_id,
            label=label,
            network="meshcore",
            state="registered",
            last_heard_utc=contact.get("lastmod_utc") or contact.get("last_advert_utc"),
            detail="registered",
        )

    registered_users = sorted(
        users_by_key.values(),
        key=lambda item: str(item.get("last_heard_utc") or item.get("last_event_utc") or ""),
        reverse=True,
    )
    return registered_users[:limit]


def build_sms_gateway_payload(app: BasestationApp) -> tuple[HTTPStatus, dict[str, Any]]:
    gateway = _get_sms_gateway(app)
    if gateway is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "ok": False,
            "available": False,
            "warning": "SMS gateway is not enabled.",
        }

    inbound_events = [
        _augment_sms_gateway_inbound_event(app, event)
        for event in gateway.list_inbound_events(limit=20)
        if isinstance(event, dict)
    ]
    outbound_messages = [
        {
            "message_id": item.get("message_id"),
            "to_number": item.get("to_number"),
            "state": item.get("state"),
            "requested_at": item.get("requested_at"),
            "claimed_at": item.get("claimed_at"),
            "updated_at": item.get("updated_at"),
            "provider_message_id": item.get("provider_message_id"),
            "detail": item.get("detail"),
            "content_visibility": "redacted",
        }
        for item in gateway.list_outbound_messages(limit=20)
        if isinstance(item, dict)
    ]
    gateway_status = dict(gateway.get_status())
    phone_link_state = str(gateway_status.get("phone_link_state") or "").strip() or _derive_sms_phone_link_state(gateway_status)
    gateway_status["phone_link_state"] = phone_link_state
    gateway_status.setdefault("last_link_status_at", gateway_status.get("last_android_seen_at"))
    gateway_status.setdefault("last_outbound_claim_at", None)
    gateway_status.setdefault("last_outbound_completion_at", None)
    gateway_status.setdefault("last_inbound_received_at", inbound_events[0].get("observed_at_utc") if inbound_events else None)
    current_status = str(gateway_status.get("status") or "").strip()
    if current_status in {"", "idle"}:
        gateway_status["status"] = _derive_sms_gateway_status(gateway_status, phone_link_state=phone_link_state)
    basestation_events = [
        event for event in app.message_events.list_events()
        if str(event.get("network") or "").lower() in {"sms", "rcs"}
    ]
    basestation_events = basestation_events[-20:]

    return HTTPStatus.OK, {
        "ok": True,
        "available": True,
        "gateway": gateway_status,
        "inbound_count": len(inbound_events),
        "outbound_count": len(outbound_messages),
        "inbound_events": inbound_events,
        "outbound_messages": outbound_messages,
        "basestation_events": basestation_events,
    }


def _redact_workbench_delivery_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": row.get("message_id"),
        "sender_user_id": row.get("sender_user_id"),
        "target_user_id": row.get("target_user_id"),
        "target_endpoint_network": row.get("target_endpoint_network"),
        "has_target_endpoint": bool(str(row.get("target_endpoint_address") or "").strip()),
        "conversation_scope": row.get("conversation_scope"),
        "message_class": row.get("message_class"),
        "delivery_state": row.get("delivery_state"),
        "selected_route": row.get("selected_route"),
        "created_at": row.get("created_at"),
        "next_attempt_after": row.get("next_attempt_after"),
        "last_attempt_at": row.get("last_attempt_at"),
        "last_error_code": row.get("last_error_code"),
        "accepted_station_id": row.get("accepted_station_id"),
        "owning_station_id": row.get("owning_station_id"),
        "claim_station_id": row.get("claim_station_id"),
        "claim_expires_at": row.get("claim_expires_at"),
        "updated_at": row.get("updated_at"),
    }


def _sink_endpoint_config_summary(adapter_type: str, config: dict[str, Any]) -> str | None:
    # Headers may carry collector tokens, so only the destination locator is surfaced.
    if adapter_type == "local-file":
        return str(config.get("path") or "").strip() or None
    if adapter_type == "http":
        return str(config.get("url") or "").strip() or None
    return None


def build_workbench_sinks_payload(app: BasestationApp) -> dict[str, Any]:
    endpoints_by_sink: dict[str, list[dict[str, Any]]] = {}
    for endpoint in app.delivery_store.list_sink_endpoints():
        row = endpoint.as_dict()
        row["config_summary"] = _sink_endpoint_config_summary(endpoint.adapter_type, endpoint.config)
        row["adapter_available"] = endpoint.adapter_type in app.sink_delivery.adapter_types
        endpoints_by_sink.setdefault(endpoint.sink_id, []).append(row)
    pending_by_sink: dict[str, int] = {}
    for row in app.delivery_store.list_pending_messages_for_route(route_name="sink", limit=500):
        if str(row.get("delivery_state") or "") in DeliveryStore.TERMINAL_STATES:
            continue
        sink_id = str(row.get("target_user_id") or "").removeprefix("sink:")
        pending_by_sink[sink_id] = pending_by_sink.get(sink_id, 0) + 1
    sinks = []
    for sink in app.delivery_store.list_sinks():
        row = sink.as_dict()
        row["endpoints"] = endpoints_by_sink.get(sink.sink_id, [])
        row["endpoint_count"] = len(row["endpoints"])
        row["pending_count"] = pending_by_sink.get(sink.sink_id, 0)
        sinks.append(row)
    return {
        "ok": True,
        "source": "delivery-db",
        "sink_count": len(sinks),
        "adapter_types": list(app.sink_delivery.adapter_types),
        "sinks": sinks,
    }


def dispatch_sink_queued_messages_payload(app: BasestationApp, *, limit: int) -> tuple[HTTPStatus, dict[str, Any]]:
    results = app.sink_delivery.dispatch(limit=limit)
    return HTTPStatus.OK, {
        "ok": True,
        "route": "sink",
        "dispatched_count": len(results),
        "delivered_count": sum(1 for item in results if item.get("dispatch_ok")),
        "results": results,
    }


def run_delivery_maintenance_payload(app: BasestationApp) -> tuple[HTTPStatus, dict[str, Any]]:
    summary = app.maintenance.run_once()
    return HTTPStatus.OK, {"ok": not summary.errors, "maintenance": summary.as_dict()}


def build_workbench_delivery_queue_payload(app: BasestationApp) -> dict[str, Any]:
    rows = [_redact_workbench_delivery_row(row) for row in app.delivery_store.list_pending_messages(include_content=False)]
    route_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    claimed_message_count = 0
    ready_message_count = 0
    for row in rows:
        route_name = str(row.get("selected_route") or "unassigned")
        state_name = str(row.get("delivery_state") or "unknown")
        route_counts[route_name] = route_counts.get(route_name, 0) + 1
        state_counts[state_name] = state_counts.get(state_name, 0) + 1
        if row.get("claim_station_id"):
            claimed_message_count += 1
        if row.get("has_target_endpoint"):
            ready_message_count += 1
    return {
        "ok": True,
        "source": "delivery-db",
        "database_path": str(app.delivery_store.database_path),
        "message_count": len(rows),
        "claimed_message_count": claimed_message_count,
        "ready_message_count": ready_message_count,
        "route_counts": route_counts,
        "state_counts": state_counts,
        "messages": rows[:20],
        "maintenance": {
            "interval_seconds": app.settings.maintenance_interval_seconds,
            "last_run": app.maintenance.last_run.as_dict() if app.maintenance.last_run is not None else None,
        },
    }


def build_meshcore_gateway_payload(
    settings: BasestationSettings,
    client: MeshCoreRuntimeClient,
    app: BasestationApp | None = None,
) -> dict[str, Any]:
    snapshot = client.get_snapshot()
    channels = [_normalize_channel(channel) for channel in client.list_channels() if isinstance(channel, dict)]
    private_channels = [channel for channel in channels if not channel.get("is_empty") and int(channel.get("index", 0)) != 0]
    contacts = build_contacts_payload(client).get("recent_contacts", [])
    raw_inbound_events = [event for event in client.list_gateway_inbound_events(limit=20) if isinstance(event, dict)]
    _process_meshcore_private_inbound_events(settings, client, app, events=raw_inbound_events)
    inbound = [_normalize_inbound_event(event, app) for event in raw_inbound_events]
    outbound = [_normalize_outbound_event(item, app) for item in client.list_gateway_outbound_status(limit=20) if isinstance(item, dict)]
    basestation_events: list[dict[str, Any]] = []
    if app is not None:
        basestation_events = [
            _normalize_basestation_event(event, app)
            for event in app.message_events.list_events()
            if isinstance(event, dict) and str(event.get("network") or "").lower() == "meshcore"
        ]
    queued_messages: list[dict[str, Any]] = []
    if app is not None:
        queued_messages = app.delivery_queue.list_route_messages(route_name="meshcore", include_content=False, limit=20)
    history = sorted([*inbound, *outbound, *basestation_events], key=lambda item: str(item.get("event_utc") or ""), reverse=True)[:20]
    return {
        "ok": True,
        "gateway_id": settings.meshcore_gateway_id,
        "display_name": settings.meshcore_display_name,
        "connected": bool(snapshot.get("connected")),
        "status": snapshot.get("status"),
        "runtime": _runtime_status(snapshot),
        "private_channel_count": len(private_channels),
        "private_channels": private_channels,
        "contacts": contacts,
        "queued_delivery_count": len(queued_messages),
        "queued_deliveries": queued_messages,
        "history": history,
    }


def dispatch_meshcore_queued_messages_payload(
    app: BasestationApp,
    client: MeshCoreRuntimeClient,
    *,
    limit: int,
) -> tuple[HTTPStatus, dict[str, Any]]:
    gateway = app.gateway_registry.find_provider_for_network("meshcore")
    if not isinstance(gateway, MeshCoreGatewayAdapter):
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "warning": "MeshCore gateway is not enabled."}
    results = gateway.dispatch_queued_messages(limit=max(1, min(int(limit), 50)))
    return HTTPStatus.OK, {
        "ok": True,
        "gateway_id": gateway.gateway_id,
        "dispatched_count": len(results),
        "results": results,
    }


def build_messages_payload(client: MeshCoreRuntimeClient, app: BasestationApp | None = None) -> dict[str, Any]:
    raw_inbound_events = [event for event in client.list_gateway_inbound_events(limit=50) if isinstance(event, dict)]
    if app is not None:
        _process_meshcore_private_inbound_events(app.settings, client, app, events=raw_inbound_events)
    inbound = [_normalize_inbound_event(event, app) for event in raw_inbound_events]
    outbound = [_normalize_outbound_event(item, app) for item in client.list_gateway_outbound_status(limit=50) if isinstance(item, dict)]
    basestation_events: list[dict[str, Any]] = []
    sms_gateway_status: dict[str, Any] | None = None
    sms_gateway: AndroidSmsGatewayAdapter | None = None
    if app is not None:
        basestation_events = [
            _normalize_basestation_event(event, app)
            for event in app.message_events.list_events()[-50:]
            if isinstance(event, dict)
        ]
        sms_gateway = _get_sms_gateway(app)
        if sms_gateway is not None:
            sms_gateway_status = sms_gateway.get_status()
            basestation_events.extend(
                _normalize_android_sms_outbound(item)
                for item in sms_gateway.list_outbound_messages(limit=50)
                if isinstance(item, dict)
            )
    messages = sorted([*inbound, *outbound, *basestation_events], key=lambda item: str(item.get("event_utc") or ""), reverse=True)
    contacts_payload = build_contacts_payload(client)
    registered_users = _build_registered_users(
        contacts_payload.get("recent_contacts", []) if isinstance(contacts_payload, dict) else [],
        messages,
        sms_gateway,
        app=app,
        limit=12,
    )
    return {
        "history_path": "gateway-link://message-events",
        "history_meta": path_metadata("gateway-link://message-events"),
        "message_count": len(messages),
        "messages": messages[:50],
        "registered_users": registered_users,
        "active_users": registered_users,
        "sms_gateway": sms_gateway_status,
    }


def send_meshcore_private_channel_message_payload(client: MeshCoreRuntimeClient, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    channel_index = _safe_int(request_payload.get("channel_index"))
    text = str(request_payload.get("text") or "").strip()
    if channel_index is None or channel_index <= 0:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "channel_index for a private channel is required"}
    if not text:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "text is required"}
    channel_name = str(request_payload.get("channel_name") or "").strip() or None
    accepted, message, payload = client.send_channel_message(
        channel_index=channel_index,
        channel_name=channel_name,
        text=text,
        delivery_id=str(request_payload.get("delivery_id") or "").strip() or None,
    )
    status = HTTPStatus.OK if accepted else HTTPStatus.BAD_GATEWAY
    return status, {
        "ok": accepted,
        "accepted": accepted,
        "message": message,
        "payload": payload,
    }


def send_sms_gateway_message_payload(app: BasestationApp, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    gateway = _get_sms_gateway(app)
    if gateway is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "SMS gateway is not enabled."}
    to_number = str(request_payload.get("to_number") or "").strip()
    body = str(request_payload.get("body") or "").strip()
    if not to_number:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "to_number is required"}
    if not body:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "body is required"}
    queued = gateway.queue_outbound_sms(to_number=to_number, body=body)
    return HTTPStatus.OK, {
        "ok": True,
        "accepted": True,
        "message": f"Queued outbound SMS for {to_number}.",
        "payload": {
            "message_id": queued.message_id,
            "to_number": queued.target_endpoint_address,
            "body": queued.plaintext_body,
            "requested_at": queued.created_at,
            "state": queued.delivery_state,
        },
    }


def _extract_dict(source: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = source.get(name)
        if isinstance(value, dict):
            return dict(value)
    return {}


def build_device_payload(settings: BasestationSettings, client: MeshCoreRuntimeClient) -> dict[str, Any]:
    snapshot = client.get_snapshot()
    runtime_status = _runtime_status(snapshot)
    device_details = client.get_device_details()
    identity = dict(snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {})
    device_query = _extract_dict(device_details, "device_query", "device_info")
    battery = _extract_dict(device_details, "battery", "battery_storage")
    stats = _extract_dict(device_details, "stats")
    stats_core = _extract_dict(stats, "core")
    stats_radio = _extract_dict(stats, "radio")
    stats_packets = _extract_dict(stats, "packets")
    runtime_service = runtime_status.get("service") if isinstance(runtime_status.get("service"), dict) else {}

    return {
        "gateway_id": settings.meshcore_gateway_id,
        "connected": bool(snapshot.get("connected")),
        "status": snapshot.get("status"),
        "display_name": snapshot.get("display_name"),
        "identity": identity,
        "device_query": device_query,
        "battery": battery,
        "stats": {
            "core": stats_core,
            "radio": stats_radio,
            "packets": stats_packets,
        },
        "runtime": {
            "name": snapshot.get("runtime_name"),
            "version": snapshot.get("runtime_version"),
            "contract_version": snapshot.get("contract_version"),
            "region": snapshot.get("region"),
            "transport": runtime_status.get("transport"),
            "interface_mode": runtime_status.get("interface_mode"),
            "endpoint": runtime_status.get("link_endpoint") or runtime_status.get("runtime_endpoint"),
            "service": runtime_service,
        },
        "available": {
            "device_query": bool(device_query),
            "battery": bool(battery),
            "stats_core": bool(stats_core),
            "stats_radio": bool(stats_radio),
            "stats_packets": bool(stats_packets),
        },
    }


def send_directed_private_message_payload(client: MeshCoreRuntimeClient, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    recipient_public_key = str(request_payload.get("recipient_public_key") or "").strip()
    text = str(request_payload.get("text") or "").strip()
    if not recipient_public_key:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "recipient_public_key is required"}
    if not text:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "text is required"}

    accepted, message, payload = client.send_directed_private_message(
        recipient_public_key=recipient_public_key,
        text=text,
        sender_identity=request_payload.get("sender_identity") if isinstance(request_payload.get("sender_identity"), dict) else None,
        destination_identity=request_payload.get("destination_identity") if isinstance(request_payload.get("destination_identity"), dict) else None,
        delivery_id=str(request_payload.get("delivery_id") or "").strip() or None,
        reply_context=request_payload.get("reply_context") if isinstance(request_payload.get("reply_context"), dict) else None,
    )
    status = HTTPStatus.OK if accepted else HTTPStatus.BAD_GATEWAY
    return status, {
        "ok": accepted,
        "accepted": accepted,
        "message": message,
        "payload": payload,
    }


def set_node_tx_power_payload(client: MeshCoreRuntimeClient, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    tx_power_dbm = _safe_int(request_payload.get("tx_power_dbm"))
    if tx_power_dbm is None:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "tx_power_dbm is required"}

    accepted, message, payload = client.set_tx_power(tx_power_dbm=tx_power_dbm)
    status = HTTPStatus.OK if accepted else HTTPStatus.BAD_GATEWAY
    return status, {
        "ok": accepted,
        "accepted": accepted,
        "message": message,
        "payload": payload,
    }


def save_directory_user_payload(settings: BasestationSettings, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = DirectoryStore(
        settings.active_directory_db_path,
        origin_station_id=settings.station_name,
    )
    store.initialize()
    try:
        saved = store.save_directory_user(request_payload)
    except sqlite3.IntegrityError as error:
        return HTTPStatus.CONFLICT, {
            "ok": False,
            "error": f"Directory user save rejected: {error}",
        }
    return HTTPStatus.OK, {
        "ok": True,
        "message": "Directory user saved.",
        "entry": _entry_to_directory_user(saved),
    }


def delete_directory_user_payload(settings: BasestationSettings, user_id: str) -> tuple[HTTPStatus, dict[str, Any]]:
    store = DirectoryStore(
        settings.active_directory_db_path,
        origin_station_id=settings.station_name,
    )
    store.initialize()
    deleted = store.delete_directory_user(user_id, audit_actor="workbench-preview", audit_action="delete_directory_user")
    if not deleted:
        return HTTPStatus.NOT_FOUND, {
            "ok": False,
            "error": f"Directory user {user_id} was not found.",
        }
    return HTTPStatus.OK, {
        "ok": True,
        "message": "Directory user deleted.",
        "user_id": user_id,
    }


def build_logs_payload(settings: BasestationSettings, client: MeshCoreRuntimeClient) -> dict[str, Any]:
    snapshot = client.get_snapshot()
    runtime_status = _runtime_status(snapshot)
    lines = [
        f"{iso_utc()} gateway_id={settings.meshcore_gateway_id}",
        f"{iso_utc()} status={snapshot.get('status', 'unknown')}",
        f"{iso_utc()} carrier={runtime_status.get('link_carrier', settings.meshcore_link_carrier)}",
        f"{iso_utc()} endpoint={runtime_status.get('link_endpoint', settings.meshcore_link_endpoint or settings.meshcore_base_url)}",
        f"{iso_utc()} contract_version={snapshot.get('contract_version', settings.meshcore_contract_version)}",
    ]
    last_error = snapshot.get("last_error")
    if last_error:
        lines.append(f"{iso_utc()} last_error={last_error}")
    return {
        "available": True,
        "lines": lines,
        "stderr": "",
        "limit": len(lines),
    }


def _directory_preview_rows() -> list[dict[str, Any]]:
    return [
        {
            "user": {
                "user_id": "user-8f2c1d73a4be",
                "display_name": "Jane Example",
                "status": "active",
                "home_region": "East Sector",
                "is_local_to_station": "False",
                "retention_class": "TRUSTED_STATION",
                "promotion_status": "PROMOTED",
                "created_at": "2026-08-28T10:15:00+00:00",
                "updated_at": "2026-09-01T14:22:00+00:00",
            },
            "identity": {
                "identity_id": "identity-jane-example",
                "user_id": "user-8f2c1d73a4be",
                "identity_type": "USER",
                "display_name": "jane-1234",
                "messaging_mode": "BOTH",
                "policy_id": "standard-member",
                "status": "active",
                "created_at": "2026-08-28T10:15:00+00:00",
                "updated_at": "2026-09-01T14:22:00+00:00",
            },
            "endpoints": [
                {
                    "endpoint_id": "endpoint-jane-meshcore",
                    "identity_id": "identity-jane-example",
                    "network": "MESHCORE",
                    "native_address": "9af3d182...b6cc77d2",
                    "verified": "True",
                    "enabled": "True",
                    "priority": 100,
                    "trust_level": "verified",
                    "replication_class": "TRUSTED_STATION",
                    "last_verified_at": "2026-08-31T18:10:00+00:00",
                    "capability_tags": "group,direct",
                    "updated_at": "2026-09-01T14:15:00+00:00",
                },
                {
                    "endpoint_id": "endpoint-jane-meshtastic",
                    "identity_id": "identity-jane-example",
                    "network": "MESHTASTIC",
                    "native_address": "!c0ffee12",
                    "verified": "True",
                    "enabled": "True",
                    "priority": 90,
                    "trust_level": "verified",
                    "replication_class": "TRUSTED_STATION",
                    "last_verified_at": "2026-08-31T18:00:00+00:00",
                    "capability_tags": "group,direct",
                    "updated_at": "2026-09-01T13:58:00+00:00",
                },
                {
                    "endpoint_id": "endpoint-jane-aprs",
                    "identity_id": "identity-jane-example",
                    "network": "APRS",
                    "native_address": "M0ABC-7",
                    "verified": "False",
                    "enabled": "False",
                    "priority": 40,
                    "trust_level": "pending",
                    "replication_class": "FIELD_OPERATIONAL",
                    "last_verified_at": "",
                    "capability_tags": "limited",
                    "updated_at": "2026-08-30T09:02:00+00:00",
                },
            ],
        },
        {
            "user": {
                "user_id": "user-b1a72d04321e",
                "display_name": "Unknown Medic 2",
                "status": "active",
                "home_region": "Forward Team",
                "is_local_to_station": "True",
                "retention_class": "FIELD_OPERATIONAL",
                "promotion_status": "PENDING_AUTHORITY_REVIEW",
                "created_at": "2026-09-01T11:07:00+00:00",
                "updated_at": "2026-09-01T11:07:00+00:00",
            },
            "identity": {
                "identity_id": "identity-unknown-medic-2",
                "user_id": "user-b1a72d04321e",
                "identity_type": "USER",
                "display_name": "medic-2002",
                "messaging_mode": "BOTH",
                "policy_id": None,
                "status": "active",
                "created_at": "2026-09-01T11:07:00+00:00",
                "updated_at": "2026-09-01T11:07:00+00:00",
            },
            "endpoints": [
                {
                    "endpoint_id": "endpoint-unknown-medic-2-meshcore",
                    "identity_id": "identity-unknown-medic-2",
                    "network": "MESHCORE",
                    "native_address": "ab12cd34ef56",
                    "verified": "False",
                    "enabled": "True",
                    "priority": 100,
                    "trust_level": "local",
                    "replication_class": "FIELD_OPERATIONAL",
                    "last_verified_at": "",
                    "capability_tags": "direct",
                    "updated_at": "2026-09-01T11:07:00+00:00",
                },
            ],
        },
    ]


def _entry_to_directory_user(entry: LocalUserEntry) -> dict[str, Any]:
    payload = entry.as_dict()
    endpoints = payload.get("endpoints") if isinstance(payload.get("endpoints"), list) else []
    return {
        **payload,
        "endpoint_count": len(endpoints),
        "networks": [str(endpoint.get("network") or "") for endpoint in endpoints],
        "last_activity_at": payload["user"].get("updated_at"),
    }


def build_directory_payload(settings: BasestationSettings) -> dict[str, Any]:
    store = DirectoryStore(
        settings.active_directory_db_path,
        origin_station_id=settings.station_name,
    )
    store.initialize()
    rows = [_entry_to_directory_user(entry) for entry in store.list_users()]
    is_preview = False

    promotion_counts: dict[str, int] = {}
    for row in rows:
        user = row.get("user") if isinstance(row.get("user"), dict) else {}
        promotion_status = str(user.get("promotion_status") or "UNKNOWN")
        promotion_counts[promotion_status] = promotion_counts.get(promotion_status, 0) + 1

    return {
        "source": "directory-db",
        "is_preview": is_preview,
        "database_path": str(settings.active_directory_db_path),
        "station_name": settings.station_name,
        "station_role": settings.station_role,
        "user_count": len(rows),
        "promotion_counts": promotion_counts,
        "users": rows,
    }


@dataclass(slots=True)
class WorkbenchConfig:
    settings: BasestationSettings
    static_dir: Path
    app: BasestationApp
    process_registry: "LocalProcessRegistry"
    listen_host: str
    listen_port: int
    started_at_utc: str


@dataclass(frozen=True, slots=True)
class ManagedProcessSpec:
    process_id: str
    label: str
    kind: str
    host: str
    port: int
    control_mode: str
    command: tuple[str, ...] | None = None
    notes: str | None = None


class LocalProcessRegistry:
    def __init__(self, settings: BasestationSettings, *, workbench_host: str, workbench_port: int) -> None:
        self._settings = settings
        self._workbench_host = workbench_host
        self._workbench_port = workbench_port
        self._lock = threading.Lock()
        self._managed_processes: dict[str, subprocess.Popen[bytes]] = {}

    def build_payload(self) -> dict[str, Any]:
        processes = [self._build_process_payload(spec) for spec in self._specs()]
        return {
            "ok": True,
            "host_scope": "local",
            "observed_at_utc": iso_utc(),
            "processes": processes,
        }

    def control(self, *, process_id: str, action: str) -> tuple[HTTPStatus, dict[str, Any]]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"start", "stop"}:
            return HTTPStatus.BAD_REQUEST, {
                "ok": False,
                "error": f"Unsupported action {action!r}.",
            }

        spec = next((item for item in self._specs() if item.process_id == process_id), None)
        if spec is None:
            return HTTPStatus.NOT_FOUND, {
                "ok": False,
                "error": f"Unknown basestation process {process_id}.",
            }
        if spec.control_mode != "managed":
            return HTTPStatus.CONFLICT, {
                "ok": False,
                "error": f"{spec.label} is observe-only in this UI.",
                "process": self._build_process_payload(spec),
            }

        with self._lock:
            state = self._current_state(spec)
            if normalized_action == "start":
                if state["status"] == "running":
                    return HTTPStatus.CONFLICT, {
                        "ok": False,
                        "error": f"{spec.label} is already running.",
                        "process": self._build_process_payload(spec),
                    }
                if state["status"] == "running_unmanaged":
                    return HTTPStatus.CONFLICT, {
                        "ok": False,
                        "error": f"{spec.label} is already running outside this workbench instance.",
                        "process": self._build_process_payload(spec),
                    }
                spawned = self._start_process(spec)
                return HTTPStatus.OK, {
                    "ok": True,
                    "message": f"Started {spec.label}.",
                    "process": self._build_process_payload(spec, managed_process=spawned),
                }

            if state["status"] != "running":
                detail = "outside this workbench instance" if state["status"] == "running_unmanaged" else "because it is not running"
                return HTTPStatus.CONFLICT, {
                    "ok": False,
                    "error": f"Cannot stop {spec.label} {detail}.",
                    "process": self._build_process_payload(spec),
                }
            self._stop_process(spec)
            return HTTPStatus.OK, {
                "ok": True,
                "message": f"Stopped {spec.label}.",
                "process": self._build_process_payload(spec),
            }

    def _specs(self) -> tuple[ManagedProcessSpec, ...]:
        authority_host = "127.0.0.1"
        authority_port = 8105
        return (
            ManagedProcessSpec(
                process_id="workbench-preview",
                label="Workbench Preview",
                kind="ui",
                host=self._workbench_host,
                port=self._workbench_port,
                control_mode="observe_only",
                notes="This is the current basestation UI process. It is not startable or stoppable from inside itself.",
            ),
            ManagedProcessSpec(
                process_id="authority-console",
                label="Authority Console",
                kind="authority",
                host=authority_host,
                port=authority_port,
                control_mode="managed",
                command=(
                    sys.executable,
                    "-m",
                    "ehn_basestation.app.cli",
                    "run-authority-console",
                    "--root",
                    str(self._settings.project_root),
                    "--host",
                    authority_host,
                    "--port",
                    str(authority_port),
                ),
                notes="Local authority DB console on this host.",
            ),
            ManagedProcessSpec(
                process_id="peer-link-server",
                label="Peer Link Server",
                kind="station-link",
                host=str(self._settings.station_link_bind_host or "127.0.0.1"),
                port=int(self._settings.station_link_port),
                control_mode="managed",
                command=(
                    sys.executable,
                    "-m",
                    "ehn_basestation.app.cli",
                    "run-peer-link-server",
                    "--root",
                    str(self._settings.project_root),
                    "--host",
                    str(self._settings.station_link_bind_host or "127.0.0.1"),
                    "--port",
                    str(int(self._settings.station_link_port)),
                ),
                notes="Local basestation peer-sync listener.",
            ),
        )

    def _build_process_payload(
        self,
        spec: ManagedProcessSpec,
        *,
        managed_process: subprocess.Popen[bytes] | None = None,
    ) -> dict[str, Any]:
        state = self._current_state(spec, managed_process=managed_process)
        return {
            "id": spec.process_id,
            "label": spec.label,
            "kind": spec.kind,
            "host_scope": "local",
            "host": spec.host,
            "port": spec.port,
            "status": state["status"],
            "status_label": state["status_label"],
            "pid": state["pid"],
            "control_available": bool(state["control_available"]),
            "control_reason": state["control_reason"],
            "managed_by_workbench": bool(state["managed_by_workbench"]),
            "notes": spec.notes,
        }

    def _current_state(
        self,
        spec: ManagedProcessSpec,
        *,
        managed_process: subprocess.Popen[bytes] | None = None,
    ) -> dict[str, Any]:
        process = managed_process if managed_process is not None else self._managed_processes.get(spec.process_id)
        managed_running = False
        pid: int | None = None
        if process is not None:
            if process.poll() is None:
                managed_running = True
                pid = process.pid
            else:
                self._managed_processes.pop(spec.process_id, None)
                process = None

        if spec.process_id == "workbench-preview":
            return {
                "status": "running",
                "status_label": "Running (current UI)",
                "pid": os.getpid(),
                "control_available": False,
                "control_reason": "This workbench instance serves the current UI session and remains observe-only here.",
                "managed_by_workbench": True,
            }

        port_open = _is_local_port_open(spec.host, spec.port)
        if managed_running:
            return {
                "status": "running",
                "status_label": "Running",
                "pid": pid,
                "control_available": True,
                "control_reason": "Managed by this workbench instance.",
                "managed_by_workbench": True,
            }
        if port_open:
            return {
                "status": "running_unmanaged",
                "status_label": "Running (unmanaged)",
                "pid": None,
                "control_available": False,
                "control_reason": "A listener is already present on the expected port, but it was not started by this workbench instance.",
                "managed_by_workbench": False,
            }
        return {
            "status": "stopped",
            "status_label": "Stopped",
            "pid": None,
            "control_available": True,
            "control_reason": "Start and stop are available for local basestation-owned helpers started by this workbench.",
            "managed_by_workbench": False,
        }

    def _start_process(self, spec: ManagedProcessSpec) -> subprocess.Popen[bytes]:
        if spec.command is None:
            raise RuntimeError(f"No launch command is defined for {spec.process_id}.")
        env = dict(os.environ)
        src_path = str((self._settings.project_root / "src").resolve())
        existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = src_path if not existing_pythonpath else os.pathsep.join([src_path, existing_pythonpath])
        spawned = subprocess.Popen(
            spec.command,
            cwd=str(self._settings.project_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._managed_processes[spec.process_id] = spawned
        return spawned

    def _stop_process(self, spec: ManagedProcessSpec) -> None:
        process = self._managed_processes.get(spec.process_id)
        if process is None:
            raise RuntimeError(f"No managed process is running for {spec.process_id}.")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self._managed_processes.pop(spec.process_id, None)


def _is_local_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def build_basestation_processes_payload(config: WorkbenchConfig) -> dict[str, Any]:
    return config.process_registry.build_payload()


def control_basestation_process_payload(config: WorkbenchConfig, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    process_id = str(request_payload.get("process_id") or "").strip()
    action = str(request_payload.get("action") or "").strip().lower()
    if not process_id:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "process_id is required."}
    if not action:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "action is required."}
    return config.process_registry.control(process_id=process_id, action=action)


def build_workbench_identity_payload(config: WorkbenchConfig) -> dict[str, Any]:
    return {
        "ok": True,
        "service": "workbench-preview",
        "station_name": config.settings.station_name,
        "station_role": config.settings.station_role,
        "pid": os.getpid(),
        "started_at_utc": config.started_at_utc,
        "listen_host": config.listen_host,
        "listen_port": config.listen_port,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "module_path": str(Path(__file__).resolve()),
        "project_root": str(config.settings.project_root),
        "static_dir": str(config.static_dir.resolve()),
        "meshcore_link_carrier": config.settings.meshcore_link_carrier,
        "meshcore_link_endpoint": config.settings.meshcore_link_endpoint or config.settings.meshcore_base_url,
    }


def _get_sms_gateway(app: BasestationApp) -> AndroidSmsGatewayAdapter | None:
    gateway = app.gateway_registry.find_provider_for_network("sms")
    if isinstance(gateway, AndroidSmsGatewayAdapter):
        return gateway
    return None


def _normalize_android_auth_token(authorization: str | None, fallback_token: str | None) -> str | None:
    header = str(authorization or "").strip()
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    fallback = str(fallback_token or "").strip()
    return fallback or None


def is_android_sms_request_authorized(settings: BasestationSettings, headers: dict[str, str]) -> bool:
    expected = settings.sms_android_auth_token
    if not expected:
        return False
    received = _normalize_android_auth_token(
        headers.get("Authorization"),
        headers.get("X-Android-Gateway-Token"),
    )
    return bool(received and received == expected)


def build_android_sms_link_status_payload(
    settings: BasestationSettings,
    gateway: AndroidSmsGatewayAdapter,
    *,
    observed_base_url: str,
) -> dict[str, Any]:
    gateway.note_android_seen()
    status = gateway.get_status()
    return {
        "ok": True,
        "gateway_id": gateway.gateway_id,
        "network": "sms",
        "display_name": settings.sms_display_name,
        "basestation": {
            "gateway_id": settings.station_name,
            "station_role": settings.station_role,
            "observed_base_url": observed_base_url,
        },
        "status": status,
    }


def receive_android_sms_inbound_payload(
    app: BasestationApp,
    request_payload: dict[str, Any],
    *,
    settings: BasestationSettings | None = None,
    meshcore_client: MeshCoreRuntimeClient | None = None,
) -> tuple[HTTPStatus, dict[str, Any]]:
    gateway = _get_sms_gateway(app)
    if gateway is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "warning": "SMS gateway is not enabled."}

    from_number = str(request_payload.get("from_number") or "").strip() or None
    from_name = str(request_payload.get("from_name") or "").strip() or None
    body = str(request_payload.get("body") or "").strip()
    transport = str(request_payload.get("transport") or "sms").strip().lower() or "sms"
    observed_at_utc = str(request_payload.get("observed_at_utc") or "").strip() or utc_now_iso()
    if not body:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "warning": "body is required"}
    sender_endpoint = from_number or from_name or ""
    if not sender_endpoint:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "warning": "from_number or from_name is required"}

    duplicate = gateway.find_recent_duplicate_notification_event(
        body=body,
        transport=transport,
        observed_at_utc=observed_at_utc,
    )
    if duplicate is not None:
        return HTTPStatus.OK, {
            "ok": True,
            "duplicate_suppressed": True,
            "message": "Probable notification-derived duplicate suppressed.",
            "matched_event_id": duplicate.get("event_id"),
            "matched_transport": duplicate.get("network"),
        }

    inbound_event = gateway.record_inbound_message(
        from_number=from_number,
        from_name=from_name,
        body=body,
        transport=transport,
        observed_at_utc=observed_at_utc,
    )

    result = app.inbound_router.ingest_inbound_text_message(
        InboundTextMessage(
            gateway_id=gateway.gateway_id,
            network=transport,
            sender_endpoint=sender_endpoint,
            text=body,
            sender_display_name=from_name,
        )
    )
    classification = result.classification
    inbound_event["sender_user_id"] = result.sender_entry.user.user_id if result.sender_entry is not None else None
    inbound_event["sender_label"] = _identity_label_for_entry(result.sender_entry) if result.sender_entry is not None else from_number or from_name
    inbound_event["sender_display_name"] = result.sender_entry.user.display_name if result.sender_entry is not None else from_name
    inbound_event["target_id"] = classification.target.target_id if classification.target is not None else None
    inbound_event["sender_resolution"] = result.sender_resolution
    meshcore_delivery: dict[str, Any] | None = None
    reply_queued = False
    queued_message_id: str | None = None
    registration_reply: str | None = None
    preferred_language = _preferred_language_for_entry(result.sender_entry)
    if classification.action == "register" and transport == "sms" and from_number:
        registration_reply = app.reply_templates.render(
            "registered" if result.sender_entry is not None else "unknown_user",
            preferred_language=preferred_language,
        )
        queued = gateway.queue_outbound_sms(to_number=from_number, body=registration_reply)
        reply_queued = True
        queued_message_id = queued.message_id
    if settings is not None and transport in {"sms", "rcs"} and classification.action == "route":
        meshcore_delivery = route_inbound_sms_to_meshcore_payload(
            app,
            settings,
            meshcore_client,
            from_number=from_number,
            from_name=from_name,
            body=classification.body,
            routing_result=result,
        )
        if from_number and isinstance(meshcore_delivery, dict) and meshcore_delivery.get("status") == "accepted_but_queued":
            registration_reply = app.reply_templates.render(
                "message_stored",
                preferred_language=preferred_language,
            )
            queued = gateway.queue_outbound_sms(to_number=from_number, body=registration_reply)
            reply_queued = True
            queued_message_id = queued.message_id
        elif from_number and isinstance(meshcore_delivery, dict) and meshcore_delivery.get("status") == "unknown_target":
            registration_reply = app.reply_templates.render(
                "unknown_user",
                preferred_language=preferred_language,
            )
            queued = gateway.queue_outbound_sms(to_number=from_number, body=registration_reply)
            reply_queued = True
            queued_message_id = queued.message_id
    return HTTPStatus.OK, {
        "ok": True,
        "action": classification.action,
        "reason": classification.reason,
        "is_valid_ehn": classification.is_valid_ehn,
        "duplicate_suppressed": False,
        "presence_only": classification.action == "register",
        "conversation_scope": classification.conversation_scope,
        "target": {
            "target_type": classification.target.target_type,
            "target_id": classification.target.target_id,
        } if classification.target else None,
        "sender_resolution": result.sender_resolution,
        "registration_reply": registration_reply,
        "reply_queued": reply_queued,
        "queued_message_id": queued_message_id,
        "meshcore_delivery": meshcore_delivery,
    }


def claim_android_sms_outbound_payload(
    app: BasestationApp,
    *,
    limit: int,
) -> tuple[HTTPStatus, dict[str, Any]]:
    gateway = _get_sms_gateway(app)
    if gateway is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "warning": "SMS gateway is not enabled."}
    messages = gateway.claim_outbound_messages(limit=max(1, min(int(limit), 50)))
    return HTTPStatus.OK, {
        "ok": True,
        "gateway_id": gateway.gateway_id,
        "messages": messages,
    }


def complete_android_sms_outbound_payload(
    app: BasestationApp,
    *,
    message_id: str,
    request_payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]]:
    gateway = _get_sms_gateway(app)
    if gateway is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "warning": "SMS gateway is not enabled."}
    completion = gateway.complete_outbound_message(
        message_id=message_id,
        ok=bool(request_payload.get("ok")),
        provider_message_id=str(request_payload.get("provider_message_id") or "").strip() or None,
        detail=str(request_payload.get("detail") or "").strip() or None,
    )
    if completion is None:
        return HTTPStatus.NOT_FOUND, {"ok": False, "warning": f"Unknown outbound message_id {message_id}."}
    return HTTPStatus.OK, {"ok": True, "completion": completion}


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "EHNWorkbench/0.1"

    @property
    def config(self) -> WorkbenchConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self._log_http_access("GET", parsed.path)
        client = MeshCoreRuntimeClient.from_settings(self.config.settings)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/runtime":
            self.respond_json(build_runtime_payload(self.config.settings, client))
            return
        if parsed.path == "/api/node":
            self.respond_json(build_node_payload(self.config.settings, client))
            return
        if parsed.path == "/api/contacts":
            self.respond_json(build_contacts_payload(client))
            return
        if parsed.path == "/api/messages":
            self.respond_json(build_messages_payload(client, self.config.app))
            return
        if parsed.path == "/api/logs":
            self.respond_json(build_logs_payload(self.config.settings, client))
            return
        if parsed.path == "/api/device":
            self.respond_json(build_device_payload(self.config.settings, client))
            return
        if parsed.path == "/api/gateways/meshcore":
            self.respond_json(build_meshcore_gateway_payload(self.config.settings, client, self.config.app))
            return
        if parsed.path == "/api/gateways/sms":
            status, response = build_sms_gateway_payload(self.config.app)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/delivery/queue":
            self.respond_json(build_workbench_delivery_queue_payload(self.config.app))
            return
        if parsed.path == "/api/delivery/sinks":
            self.respond_json(build_workbench_sinks_payload(self.config.app))
            return
        if parsed.path == "/api/directory":
            self.respond_json(build_directory_payload(self.config.settings))
            return
        if parsed.path == "/api/basestation/processes":
            self.respond_json(build_basestation_processes_payload(self.config))
            return
        if parsed.path == "/api/workbench/identity":
            self.respond_json(build_workbench_identity_payload(self.config))
            return
        if parsed.path == "/api/session":
            snapshot = client.get_snapshot()
            self.respond_json({
                "gateway_id": self.config.settings.meshcore_gateway_id,
                "connected": bool(snapshot.get("connected")),
                "status": snapshot.get("status"),
                "transport": _runtime_status(snapshot),
            })
            return
        if parsed.path == "/api/sms/android/link-status":
            gateway = _get_sms_gateway(self.config.app)
            authorized = is_android_sms_request_authorized(self.config.settings, self._request_headers_dict())
            if gateway is not None:
                gateway.note_android_request(
                    path=parsed.path,
                    authorized=authorized,
                    observed_base_url=self._observed_base_url(),
                    remote_addr=self.client_address[0] if self.client_address else None,
                )
            self._log_android_sms_request(parsed.path, authorized=authorized)
            if not authorized:
                self.respond_json({"ok": False, "warning": "Unauthorized Android SMS gateway request."}, status=HTTPStatus.UNAUTHORIZED)
                return
            if gateway is None:
                self.respond_json({"ok": False, "warning": "SMS gateway is not enabled."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            observed_base_url = self._observed_base_url()
            self.respond_json(build_android_sms_link_status_payload(self.config.settings, gateway, observed_base_url=observed_base_url))
            return
        if parsed.path == "/api/sms/android/outbound":
            gateway = _get_sms_gateway(self.config.app)
            authorized = is_android_sms_request_authorized(self.config.settings, self._request_headers_dict())
            if gateway is not None:
                gateway.note_android_request(
                    path=parsed.path,
                    authorized=authorized,
                    observed_base_url=self._observed_base_url(),
                    remote_addr=self.client_address[0] if self.client_address else None,
                )
            self._log_android_sms_request(parsed.path, authorized=authorized)
            if not authorized:
                self.respond_json({"ok": False, "warning": "Unauthorized Android SMS gateway request."}, status=HTTPStatus.UNAUTHORIZED)
                return
            limit = _safe_int((query.get("limit") or [10])[0]) or 10
            status, response = claim_android_sms_outbound_payload(self.config.app, limit=limit)
            self.respond_json(response, status=status)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        self._log_http_access("POST", parsed.path)
        client = MeshCoreRuntimeClient.from_settings(self.config.settings)
        if parsed.path == "/api/messages/directed-private":
            payload = self.read_json_body()
            status, response = send_directed_private_message_payload(client, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/node/tx-power":
            payload = self.read_json_body()
            status, response = set_node_tx_power_payload(client, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/directory/user":
            payload = self.read_json_body()
            status, response = save_directory_user_payload(self.config.settings, payload)
            self.respond_json(response, status=status)
            return
        directory_delete_prefix = "/api/directory/user/"
        directory_delete_suffix = "/delete"
        if parsed.path.startswith(directory_delete_prefix) and parsed.path.endswith(directory_delete_suffix):
            user_id = unquote(parsed.path[len(directory_delete_prefix):-len(directory_delete_suffix)]).strip()
            status, response = delete_directory_user_payload(self.config.settings, user_id)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/gateways/meshcore/private-channel-send":
            payload = self.read_json_body()
            status, response = send_meshcore_private_channel_message_payload(client, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/gateways/meshcore/queued-dispatch":
            payload = self.read_json_body()
            limit = _safe_int(payload.get("limit")) or 10
            status, response = dispatch_meshcore_queued_messages_payload(self.config.app, client, limit=limit)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/delivery/sinks/dispatch":
            payload = self.read_json_body()
            limit = _safe_int(payload.get("limit")) or 10
            status, response = dispatch_sink_queued_messages_payload(self.config.app, limit=limit)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/delivery/maintenance":
            status, response = run_delivery_maintenance_payload(self.config.app)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/gateways/sms/send":
            payload = self.read_json_body()
            status, response = send_sms_gateway_message_payload(self.config.app, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/basestation/process-control":
            payload = self.read_json_body()
            status, response = control_basestation_process_payload(self.config, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/sms/android/inbound":
            gateway = _get_sms_gateway(self.config.app)
            authorized = is_android_sms_request_authorized(self.config.settings, self._request_headers_dict())
            if gateway is not None:
                gateway.note_android_request(
                    path=parsed.path,
                    authorized=authorized,
                    observed_base_url=self._observed_base_url(),
                    remote_addr=self.client_address[0] if self.client_address else None,
                )
            self._log_android_sms_request(parsed.path, authorized=authorized)
            if not authorized:
                self.respond_json({"ok": False, "warning": "Unauthorized Android SMS gateway request."}, status=HTTPStatus.UNAUTHORIZED)
                return
            payload = self.read_json_body()
            status, response = receive_android_sms_inbound_payload(
                self.config.app,
                payload,
                settings=self.config.settings,
                meshcore_client=client,
            )
            self.respond_json(response, status=status)
            return
        if parsed.path.startswith("/api/sms/android/outbound/") and parsed.path.endswith("/complete"):
            gateway = _get_sms_gateway(self.config.app)
            authorized = is_android_sms_request_authorized(self.config.settings, self._request_headers_dict())
            if gateway is not None:
                gateway.note_android_request(
                    path=parsed.path,
                    authorized=authorized,
                    observed_base_url=self._observed_base_url(),
                    remote_addr=self.client_address[0] if self.client_address else None,
                )
            self._log_android_sms_request(parsed.path, authorized=authorized)
            if not authorized:
                self.respond_json({"ok": False, "warning": "Unauthorized Android SMS gateway request."}, status=HTTPStatus.UNAUTHORIZED)
                return
            payload = self.read_json_body()
            message_id = parsed.path.removeprefix("/api/sms/android/outbound/").removesuffix("/complete").strip("/")
            status, response = complete_android_sms_outbound_payload(self.config.app, message_id=message_id, request_payload=payload)
            self.respond_json(response, status=status)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        self._log_http_access("DELETE", parsed.path)
        directory_delete_prefix = "/api/directory/user/"
        if parsed.path.startswith(directory_delete_prefix):
            user_id = unquote(parsed.path[len(directory_delete_prefix):]).strip()
            status, response = delete_directory_user_payload(self.config.settings, user_id)
            self.respond_json(response, status=status)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(content_length) if content_length > 0 else b""
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _request_headers_dict(self) -> dict[str, str]:
        return {name: value for name, value in self.headers.items()}

    def _log_http_access(self, method: str, path: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        remote_addr = self.client_address[0] if self.client_address else "unknown"
        user_agent = str(self.headers.get("User-Agent") or "-").strip()
        print(
            f"[{timestamp}] http access method={method} path={path} remote={remote_addr} ua={user_agent}",
            flush=True,
        )

    def _log_android_sms_request(self, path: str, *, authorized: bool) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        remote_addr = self.client_address[0] if self.client_address else "unknown"
        print(
            f"[{timestamp}] android-sms request path={path} remote={remote_addr} authorized={authorized}",
            flush=True,
        )

    def _observed_base_url(self) -> str:
        host = str(self.headers.get("Host") or "").strip()
        if not host:
            host = f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        return f"http://{host}"

    def serve_static(self, request_path: str) -> None:
        path = request_path or "/"
        candidate = self.config.static_dir / "workbench-preview.html" if path == "/" else (self.config.static_dir / path.lstrip("/")).resolve()
        if self.config.static_dir.resolve() not in candidate.parents and candidate != self.config.static_dir.resolve() / "workbench-preview.html":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not candidate.exists() or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body = candidate.read_bytes()
        content_type, _ = mimetypes.guess_type(candidate.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (content_type or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], config: WorkbenchConfig):
        super().__init__(server_address, handler_class)
        self.config = config


def run_workbench_server(*, settings: BasestationSettings, host: str, port: int) -> None:
    config = WorkbenchConfig(
        settings=settings,
        static_dir=STATIC_DIR,
        app=create_app(settings=settings),
        process_registry=LocalProcessRegistry(settings, workbench_host=host, workbench_port=port),
        listen_host=host,
        listen_port=port,
        started_at_utc=iso_utc(),
    )
    server = WorkbenchServer((host, port), WorkbenchHandler, config)
    scheduler: MaintenanceScheduler | None = None
    if settings.maintenance_interval_seconds > 0:
        scheduler = MaintenanceScheduler(config.app.maintenance, interval_seconds=settings.maintenance_interval_seconds)
        scheduler.start()
    print(f"EHN MeshCore workbench listening on http://{host}:{port}")
    print(f"Static root: {config.static_dir}")
    print(f"MeshCore carrier: {settings.meshcore_link_carrier}")
    print(f"MeshCore endpoint: {settings.meshcore_link_endpoint or settings.meshcore_base_url}")
    print(f"Delivery maintenance: {'every ' + str(scheduler.interval_seconds) + 's' if scheduler else 'disabled'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if scheduler is not None:
            scheduler.stop()
        server.server_close()
