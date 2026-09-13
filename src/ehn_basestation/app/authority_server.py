from __future__ import annotations

from dataclasses import dataclass
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ehn_basestation.app.settings import BasestationSettings, resolve_project_root
from ehn_basestation.app.workbench_server import path_metadata
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.directory.station_store import StationStore


STATIC_DIR = resolve_project_root() / "ui" / "static"


def _authority_store(settings: BasestationSettings) -> DirectoryStore:
    store = DirectoryStore(settings.authority_directory_db_path, origin_station_id="authority-main")
    store.initialize()
    return store


def _registry_store(settings: BasestationSettings) -> StationStore:
    store = StationStore(settings.registry_db_path)
    store.initialize()
    return store


def _delivery_store(settings: BasestationSettings) -> DeliveryStore:
    store = DeliveryStore(settings.delivery_db_path)
    store.initialize()
    return store


def build_authority_summary_payload(settings: BasestationSettings) -> dict[str, Any]:
    store = _authority_store(settings)
    registry_store = _registry_store(settings)
    delivery_store = _delivery_store(settings)
    rows = store.list_users()
    audit_rows = store.list_audit_events(limit=50)
    promotion_counts: dict[str, int] = {}
    for row in rows:
        promotion_status = str(row.user.promotion_status or "UNKNOWN")
        promotion_counts[promotion_status] = promotion_counts.get(promotion_status, 0) + 1
    return {
        "ok": True,
        "authority": {
            "station_name": settings.station_name,
            "station_role": settings.station_role,
            "database_path": str(settings.authority_directory_db_path),
            "registry_database_path": str(settings.registry_db_path),
            "delivery_database_path": str(settings.delivery_db_path),
            "database_meta": path_metadata(str(settings.authority_directory_db_path)),
            "user_count": len(rows),
            "station_count": len(registry_store.list_station_records()),
            "queued_message_count": len(delivery_store.list_pending_messages()),
            "promotion_counts": promotion_counts,
            "audit_event_count": len(audit_rows),
            "last_audit_event_utc": audit_rows[0]["event_ts"] if audit_rows else None,
            "backup_policy": {
                "daily_backup_required": True,
                "hourly_local_copy_required": True,
                "notes": "Automation is not yet implemented; this console only establishes the authority DB boundary and audit surface.",
            },
        },
    }


def build_authority_users_payload(settings: BasestationSettings) -> dict[str, Any]:
    store = _authority_store(settings)
    rows = store.list_users()
    return {
        "ok": True,
        "source": "authority-db",
        "database_path": str(settings.authority_directory_db_path),
        "user_count": len(rows),
        "users": [entry.as_dict() for entry in rows],
    }


def build_authority_audit_payload(settings: BasestationSettings, *, limit: int = 100) -> dict[str, Any]:
    store = _authority_store(settings)
    rows = store.list_audit_events(limit=limit)
    return {
        "ok": True,
        "database_path": str(settings.authority_directory_db_path),
        "audit_count": len(rows),
        "audit_events": rows,
    }


def build_authority_stations_payload(settings: BasestationSettings) -> dict[str, Any]:
    store = _registry_store(settings)
    rows = store.list_station_records()
    return {
        "ok": True,
        "source": "registry-db",
        "database_path": str(settings.registry_db_path),
        "station_count": len(rows),
        "stations": [entry.as_dict() for entry in rows],
    }


def save_authority_station_payload(settings: BasestationSettings, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _registry_store(settings)
    station = request_payload.get("station") if isinstance(request_payload.get("station"), dict) else request_payload
    capabilities = request_payload.get("capabilities") if isinstance(request_payload.get("capabilities"), dict) else {}
    saved_station = store.upsert_station_record(
        station_id=str(station.get("station_id") or settings.station_id).strip(),
        display_name=str(station.get("display_name") or settings.station_name).strip(),
        role=str(station.get("role") or settings.station_role).strip(),
        db_path=str(station.get("db_path") or settings.active_directory_db_path).strip(),
        source=str(station.get("source") or "authority-console").strip(),
        owner_name=str(station.get("owner_name") or "").strip(),
        owner_org=str(station.get("owner_org") or "").strip(),
        deployment_status=str(station.get("deployment_status") or "active").strip() or "active",
        location_label=str(station.get("location_label") or "").strip(),
        location_lat=station.get("location_lat"),
        location_lon=station.get("location_lon"),
        notes=str(station.get("notes") or "").strip(),
    )
    saved_capabilities = store.upsert_station_capabilities(
        station_id=saved_station.station_id,
        supports_meshcore=bool(capabilities.get("supports_meshcore")),
        supports_sms_gateway=bool(capabilities.get("supports_sms_gateway")),
        supports_meshtastic=bool(capabilities.get("supports_meshtastic")),
        supports_email_fallback=bool(capabilities.get("supports_email_fallback")),
        supports_queue_claims=bool(capabilities.get("supports_queue_claims")),
        supports_encrypted_sync=bool(capabilities.get("supports_encrypted_sync")),
        last_verified_at=str(capabilities.get("last_verified_at") or "").strip() or None,
    )
    return HTTPStatus.OK, {
        "ok": True,
        "message": "Authority station saved.",
        "station": saved_station.as_dict(),
        "capabilities": saved_capabilities.as_dict(),
    }


def build_authority_delivery_messages_payload(settings: BasestationSettings) -> dict[str, Any]:
    store = _delivery_store(settings)
    rows = [_redact_authority_delivery_row(row) for row in store.list_pending_messages(include_content=False)]
    route_counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    claimed_message_count = 0
    for row in rows:
        route_name = str(row.get("selected_route") or "unassigned")
        state_name = str(row.get("delivery_state") or "unknown")
        route_counts[route_name] = route_counts.get(route_name, 0) + 1
        state_counts[state_name] = state_counts.get(state_name, 0) + 1
        if row.get("claim_station_id"):
            claimed_message_count += 1
    return {
        "ok": True,
        "source": "delivery-db",
        "database_path": str(settings.delivery_db_path),
        "message_count": len(rows),
        "claimed_message_count": claimed_message_count,
        "route_counts": route_counts,
        "state_counts": state_counts,
        "messages": rows,
    }


def _redact_authority_delivery_row(row: dict[str, Any]) -> dict[str, Any]:
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
        "fallback_route": row.get("fallback_route"),
        "created_at": row.get("created_at"),
        "next_attempt_after": row.get("next_attempt_after"),
        "fallback_after": row.get("fallback_after"),
        "expires_at": row.get("expires_at"),
        "last_attempt_at": row.get("last_attempt_at"),
        "last_error_code": row.get("last_error_code"),
        "accepted_station_id": row.get("accepted_station_id"),
        "owning_station_id": row.get("owning_station_id"),
        "claim_station_id": row.get("claim_station_id"),
        "claim_expires_at": row.get("claim_expires_at"),
        "claim_heartbeat_at": row.get("claim_heartbeat_at"),
        "created_by_event_id": row.get("created_by_event_id"),
        "updated_at": row.get("updated_at"),
    }


def create_authority_delivery_message_payload(settings: BasestationSettings, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _delivery_store(settings)
    saved = store.enqueue_message(
        sender_user_id=str(request_payload.get("sender_user_id") or "system").strip() or "system",
        target_user_id=str(request_payload.get("target_user_id") or "").strip(),
        target_endpoint_network=str(request_payload.get("target_endpoint_network") or "").strip(),
        target_endpoint_address=str(request_payload.get("target_endpoint_address") or "").strip(),
        conversation_scope=str(request_payload.get("conversation_scope") or "single").strip() or "single",
        message_class=str(request_payload.get("message_class") or "message").strip() or "message",
        plaintext_body=str(request_payload.get("plaintext_body") or ""),
        body_summary=str(request_payload.get("body_summary") or "").strip(),
        accepted_station_id=str(request_payload.get("accepted_station_id") or settings.station_id).strip(),
        owning_station_id=str(request_payload.get("owning_station_id") or settings.station_id).strip(),
        selected_route=str(request_payload.get("selected_route") or "").strip(),
        fallback_route=str(request_payload.get("fallback_route") or "").strip(),
        next_attempt_after=str(request_payload.get("next_attempt_after") or "").strip() or None,
        fallback_after=str(request_payload.get("fallback_after") or "").strip() or None,
        expires_at=str(request_payload.get("expires_at") or "").strip() or None,
        created_by_event_id=str(request_payload.get("created_by_event_id") or "").strip(),
    )
    return HTTPStatus.OK, {
        "ok": True,
        "message": "Delivery message queued.",
        "entry": _redact_authority_delivery_row(saved.as_dict(include_content=False)),
    }


def claim_authority_delivery_message_payload(settings: BasestationSettings, message_id: str, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _delivery_store(settings)
    claimed = store.claim_message(
        message_id=message_id,
        station_id=str(request_payload.get("station_id") or settings.station_id).strip(),
        claim_window_seconds=int(request_payload.get("claim_window_seconds") or 60),
    )
    if claimed is None:
        return HTTPStatus.CONFLICT, {"ok": False, "error": f"Message {message_id} is not claimable."}
    return HTTPStatus.OK, {"ok": True, "entry": claimed.as_dict(include_content=False)}


def renew_authority_delivery_message_payload(settings: BasestationSettings, message_id: str, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _delivery_store(settings)
    renewed = store.renew_message_claim(
        message_id=message_id,
        station_id=str(request_payload.get("station_id") or settings.station_id).strip(),
        claim_token=str(request_payload.get("claim_token") or "").strip(),
        claim_window_seconds=int(request_payload.get("claim_window_seconds") or 60),
    )
    if renewed is None:
        return HTTPStatus.CONFLICT, {"ok": False, "error": f"Message {message_id} claim could not be renewed."}
    return HTTPStatus.OK, {"ok": True, "entry": renewed.as_dict(include_content=False)}


def complete_authority_delivery_message_payload(settings: BasestationSettings, message_id: str, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _delivery_store(settings)
    completed = store.complete_message(
        message_id=message_id,
        station_id=str(request_payload.get("station_id") or settings.station_id).strip(),
        claim_token=str(request_payload.get("claim_token") or "").strip(),
        outcome=str(request_payload.get("outcome") or "delivered").strip(),
        route_name=str(request_payload.get("route_name") or "").strip(),
        gateway_request_id=str(request_payload.get("gateway_request_id") or "").strip(),
        outcome_detail=str(request_payload.get("outcome_detail") or "").strip(),
    )
    if completed is None:
        return HTTPStatus.CONFLICT, {"ok": False, "error": f"Message {message_id} could not be completed."}
    return HTTPStatus.OK, {"ok": True, "entry": completed.as_dict(include_content=False)}


def release_authority_delivery_message_payload(settings: BasestationSettings, message_id: str, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _delivery_store(settings)
    released = store.release_message_claim(
        message_id=message_id,
        station_id=str(request_payload.get("station_id") or settings.station_id).strip(),
        claim_token=str(request_payload.get("claim_token") or "").strip(),
        route_name=str(request_payload.get("route_name") or "").strip(),
        next_attempt_after=str(request_payload.get("next_attempt_after") or "").strip() or None,
        outcome=str(request_payload.get("outcome") or "retryable_failure").strip(),
        outcome_detail=str(request_payload.get("outcome_detail") or "").strip(),
        gateway_request_id=str(request_payload.get("gateway_request_id") or "").strip(),
    )
    if released is None:
        return HTTPStatus.CONFLICT, {"ok": False, "error": f"Message {message_id} could not be released."}
    return HTTPStatus.OK, {"ok": True, "entry": released.as_dict(include_content=False)}


def save_authority_user_payload(settings: BasestationSettings, request_payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _authority_store(settings)
    saved = store.save_directory_user(request_payload, audit_actor="authority-console", audit_action="save_authority_user")
    return HTTPStatus.OK, {
        "ok": True,
        "message": "Authority user saved.",
        "entry": saved.as_dict(),
    }


def delete_authority_user_payload(settings: BasestationSettings, user_id: str) -> tuple[HTTPStatus, dict[str, Any]]:
    store = _authority_store(settings)
    deleted = store.delete_directory_user(user_id, audit_actor="authority-console", audit_action="delete_authority_user")
    if not deleted:
        return HTTPStatus.NOT_FOUND, {
            "ok": False,
            "error": f"Authority user {user_id} was not found.",
        }
    return HTTPStatus.OK, {
        "ok": True,
        "message": "Authority user deleted.",
        "user_id": user_id,
    }


@dataclass(slots=True)
class AuthorityConfig:
    settings: BasestationSettings
    static_dir: Path


class AuthorityHandler(BaseHTTPRequestHandler):
    server_version = "EHNAuthority/0.1"

    @property
    def config(self) -> AuthorityConfig:
        return self.server.config  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/authority/summary":
            self.respond_json(build_authority_summary_payload(self.config.settings))
            return
        if parsed.path == "/api/authority/users":
            self.respond_json(build_authority_users_payload(self.config.settings))
            return
        if parsed.path == "/api/authority/stations":
            self.respond_json(build_authority_stations_payload(self.config.settings))
            return
        if parsed.path == "/api/authority/delivery/messages":
            self.respond_json(build_authority_delivery_messages_payload(self.config.settings))
            return
        if parsed.path == "/api/authority/audit":
            limit = int((query.get("limit") or [100])[0])
            self.respond_json(build_authority_audit_payload(self.config.settings, limit=limit))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/authority/users":
            payload = self.read_json_body()
            status, response = save_authority_user_payload(self.config.settings, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/authority/stations":
            payload = self.read_json_body()
            status, response = save_authority_station_payload(self.config.settings, payload)
            self.respond_json(response, status=status)
            return
        if parsed.path == "/api/authority/delivery/messages":
            payload = self.read_json_body()
            status, response = create_authority_delivery_message_payload(self.config.settings, payload)
            self.respond_json(response, status=status)
            return
        user_delete_suffix = "/delete"
        user_prefix = "/api/authority/users/"
        delivery_prefix = "/api/authority/delivery/messages/"
        if parsed.path.startswith(delivery_prefix):
            payload = self.read_json_body()
            suffix = parsed.path[len(delivery_prefix):]
            for action, handler in (
                ("/claim", claim_authority_delivery_message_payload),
                ("/renew", renew_authority_delivery_message_payload),
                ("/complete", complete_authority_delivery_message_payload),
                ("/release", release_authority_delivery_message_payload),
            ):
                if suffix.endswith(action):
                    message_id = unquote(suffix[:-len(action)]).strip()
                    status, response = handler(self.config.settings, message_id, payload)
                    self.respond_json(response, status=status)
                    return
        if parsed.path.startswith(user_prefix) and parsed.path.endswith(user_delete_suffix):
            user_id = unquote(parsed.path[len(user_prefix):-len(user_delete_suffix)]).strip()
            status, response = delete_authority_user_payload(self.config.settings, user_id)
            self.respond_json(response, status=status)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        user_prefix = "/api/authority/users/"
        if parsed.path.startswith(user_prefix):
            user_id = unquote(parsed.path[len(user_prefix):]).strip()
            status, response = delete_authority_user_payload(self.config.settings, user_id)
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

    def serve_static(self, request_path: str) -> None:
        path = request_path or "/"
        candidate = self.config.static_dir / "authority-console.html" if path == "/" else (self.config.static_dir / path.lstrip("/")).resolve()
        if self.config.static_dir.resolve() not in candidate.parents and candidate != self.config.static_dir.resolve() / "authority-console.html":
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


class AuthorityServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], config: AuthorityConfig):
        super().__init__(server_address, handler_class)
        self.config = config


def run_authority_server(*, settings: BasestationSettings, host: str, port: int) -> None:
    config = AuthorityConfig(settings=settings, static_dir=STATIC_DIR)
    server = AuthorityServer((host, port), AuthorityHandler, config)
    print(f"EHN authority console listening on http://{host}:{port}")
    print(f"Authority db: {settings.authority_directory_db_path}")
    print(f"Role: {settings.station_role}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()