from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3
import uuid


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _new_record_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _normalize_iso(timestamp: str | None) -> str | None:
    if timestamp is None:
        return None
    text = str(timestamp).strip()
    return text or None


def _parse_iso(timestamp: str | None) -> datetime | None:
    text = _normalize_iso(timestamp)
    if text is None:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class PendingMessageRecord:
    message_id: str
    sender_user_id: str
    target_user_id: str
    target_endpoint_network: str
    target_endpoint_address: str
    conversation_scope: str
    message_class: str
    plaintext_body: str
    body_summary: str
    delivery_state: str
    selected_route: str
    fallback_route: str
    created_at: str
    next_attempt_after: str | None
    fallback_after: str | None
    expires_at: str | None
    last_attempt_at: str | None
    last_error_code: str
    accepted_station_id: str
    owning_station_id: str
    claim_token: str
    claim_station_id: str
    claim_expires_at: str | None
    claim_heartbeat_at: str | None
    created_by_event_id: str
    updated_at: str

    def as_dict(self, *, include_content: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "message_id": self.message_id,
            "sender_user_id": self.sender_user_id,
            "target_user_id": self.target_user_id,
            "target_endpoint_network": self.target_endpoint_network,
            "target_endpoint_address": self.target_endpoint_address,
            "conversation_scope": self.conversation_scope,
            "message_class": self.message_class,
            "delivery_state": self.delivery_state,
            "selected_route": self.selected_route,
            "fallback_route": self.fallback_route,
            "created_at": self.created_at,
            "next_attempt_after": self.next_attempt_after,
            "fallback_after": self.fallback_after,
            "expires_at": self.expires_at,
            "last_attempt_at": self.last_attempt_at,
            "last_error_code": self.last_error_code,
            "accepted_station_id": self.accepted_station_id,
            "owning_station_id": self.owning_station_id,
            "claim_token": self.claim_token,
            "claim_station_id": self.claim_station_id,
            "claim_expires_at": self.claim_expires_at,
            "claim_heartbeat_at": self.claim_heartbeat_at,
            "created_by_event_id": self.created_by_event_id,
            "updated_at": self.updated_at,
        }
        if include_content:
            payload["plaintext_body"] = self.plaintext_body
            payload["body_summary"] = self.body_summary
        return payload


@dataclass(frozen=True, slots=True)
class UserPresenceRecord:
    user_id: str
    last_seen_at: str
    last_seen_station_id: str
    last_seen_via_network: str
    last_seen_endpoint_id: str
    presence_state: str
    presence_evidence: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DeliveryPolicyRecord:
    user_id: str
    preferred_routes: tuple[str, ...]
    fallback_email_enabled: bool
    fallback_hold_seconds: int
    retry_interval_seconds: int
    max_attempts: int
    message_expiry_seconds: int
    allow_group_fallback: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class DeliveryAttemptRecord:
    attempt_id: str
    message_id: str
    claim_token: str
    attempted_at: str
    station_id: str
    route_name: str
    outcome: str
    outcome_detail: str
    gateway_request_id: str
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "message_id": self.message_id,
            "claim_token": self.claim_token,
            "attempted_at": self.attempted_at,
            "station_id": self.station_id,
            "route_name": self.route_name,
            "outcome": self.outcome,
            "outcome_detail": self.outcome_detail,
            "gateway_request_id": self.gateway_request_id,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class SinkRecord:
    sink_id: str
    display_name: str
    owner_ref: str
    enabled: bool
    accepted_content_classes: tuple[str, ...]
    accepted_actor_classes: tuple[str, ...]
    max_body_chars: int
    notes: str
    updated_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "sink_id": self.sink_id,
            "display_name": self.display_name,
            "owner_ref": self.owner_ref,
            "enabled": self.enabled,
            "accepted_content_classes": list(self.accepted_content_classes),
            "accepted_actor_classes": list(self.accepted_actor_classes),
            "max_body_chars": self.max_body_chars,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class SinkEndpointRecord:
    sink_endpoint_id: str
    sink_id: str
    adapter_type: str
    config: dict[str, object]
    enabled: bool
    priority: int
    last_delivered_at: str | None
    updated_at: str

    def as_dict(self, *, include_config: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "sink_endpoint_id": self.sink_endpoint_id,
            "sink_id": self.sink_id,
            "adapter_type": self.adapter_type,
            "enabled": self.enabled,
            "priority": self.priority,
            "last_delivered_at": self.last_delivered_at,
            "updated_at": self.updated_at,
        }
        if include_config:
            payload["config"] = dict(self.config)
        return payload


class DeliveryStore:
    TERMINAL_STATES = {"delivered", "fallback_sent", "expired", "cancelled"}

    def __init__(self, database_path: Path) -> None:
        self._database_path = Path(database_path).resolve()

    @property
    def database_path(self) -> Path:
        return self._database_path

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS station_profiles (
                    station_id TEXT PRIMARY KEY,
                    runtime_status TEXT NOT NULL,
                    last_reported_at TEXT NOT NULL,
                    last_seen_ip TEXT NOT NULL DEFAULT '',
                    software_version TEXT NOT NULL DEFAULT '',
                    capabilities_json TEXT NOT NULL DEFAULT '{}',
                    health_summary TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS user_presence (
                    user_id TEXT PRIMARY KEY,
                    last_seen_at TEXT NOT NULL,
                    last_seen_station_id TEXT NOT NULL,
                    last_seen_via_network TEXT NOT NULL,
                    last_seen_endpoint_id TEXT NOT NULL DEFAULT '',
                    presence_state TEXT NOT NULL,
                    presence_evidence TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS delivery_policies (
                    user_id TEXT PRIMARY KEY,
                    preferred_route_json TEXT NOT NULL DEFAULT '[]',
                    fallback_email_enabled INTEGER NOT NULL DEFAULT 0,
                    fallback_hold_seconds INTEGER NOT NULL DEFAULT 0,
                    retry_interval_seconds INTEGER NOT NULL DEFAULT 300,
                    max_attempts INTEGER NOT NULL DEFAULT 10,
                    message_expiry_seconds INTEGER NOT NULL DEFAULT 86400,
                    allow_group_fallback INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_messages (
                    message_id TEXT PRIMARY KEY,
                    sender_user_id TEXT NOT NULL,
                    target_user_id TEXT NOT NULL,
                    target_endpoint_network TEXT NOT NULL DEFAULT '',
                    target_endpoint_address TEXT NOT NULL DEFAULT '',
                    conversation_scope TEXT NOT NULL,
                    message_class TEXT NOT NULL,
                    plaintext_body TEXT NOT NULL,
                    body_summary TEXT NOT NULL DEFAULT '',
                    delivery_state TEXT NOT NULL,
                    selected_route TEXT NOT NULL DEFAULT '',
                    fallback_route TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    next_attempt_after TEXT,
                    fallback_after TEXT,
                    expires_at TEXT,
                    last_attempt_at TEXT,
                    last_error_code TEXT NOT NULL DEFAULT '',
                    accepted_station_id TEXT NOT NULL,
                    owning_station_id TEXT NOT NULL,
                    claim_token TEXT NOT NULL DEFAULT '',
                    claim_station_id TEXT NOT NULL DEFAULT '',
                    claim_expires_at TEXT,
                    claim_heartbeat_at TEXT,
                    created_by_event_id TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS delivery_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    claim_token TEXT NOT NULL DEFAULT '',
                    attempted_at TEXT NOT NULL,
                    station_id TEXT NOT NULL,
                    route_name TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    outcome_detail TEXT NOT NULL DEFAULT '',
                    gateway_request_id TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reply_templates (
                    template_key TEXT NOT NULL,
                    language_code TEXT NOT NULL,
                    template_text TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (template_key, language_code)
                );

                CREATE TABLE IF NOT EXISTS sinks (
                    sink_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    owner_ref TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    accepted_content_classes_json TEXT NOT NULL DEFAULT '["telemetry", "message"]',
                    accepted_actor_classes_json TEXT NOT NULL DEFAULT '["device", "person"]',
                    max_body_chars INTEGER NOT NULL DEFAULT 1000,
                    notes TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sink_endpoints (
                    sink_endpoint_id TEXT PRIMARY KEY,
                    sink_id TEXT NOT NULL,
                    adapter_type TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 100,
                    last_delivered_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sink_endpoints_sink
                ON sink_endpoints(sink_id, enabled);

                CREATE INDEX IF NOT EXISTS idx_user_presence_station
                ON user_presence(last_seen_station_id);

                CREATE INDEX IF NOT EXISTS idx_user_presence_network
                ON user_presence(last_seen_via_network);

                CREATE INDEX IF NOT EXISTS idx_user_presence_seen_at
                ON user_presence(last_seen_at);

                CREATE INDEX IF NOT EXISTS idx_pending_target_state
                ON pending_messages(target_user_id, delivery_state);

                CREATE INDEX IF NOT EXISTS idx_pending_route_state
                ON pending_messages(selected_route, delivery_state);

                CREATE INDEX IF NOT EXISTS idx_pending_next_attempt
                ON pending_messages(next_attempt_after);

                CREATE INDEX IF NOT EXISTS idx_pending_claim_expiry
                ON pending_messages(claim_expires_at);

                CREATE INDEX IF NOT EXISTS idx_pending_owner_state
                ON pending_messages(owning_station_id, delivery_state);

                CREATE INDEX IF NOT EXISTS idx_pending_created_at
                ON pending_messages(created_at);

                CREATE INDEX IF NOT EXISTS idx_delivery_attempts_message
                ON delivery_attempts(message_id);

                CREATE INDEX IF NOT EXISTS idx_delivery_attempts_station
                ON delivery_attempts(station_id);

                CREATE INDEX IF NOT EXISTS idx_delivery_attempts_outcome
                ON delivery_attempts(outcome);
                """
            )
            self._ensure_schema_columns(connection)
            connection.commit()

    def _ensure_schema_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(pending_messages)").fetchall()
        }
        if "target_endpoint_network" not in columns:
            connection.execute(
                "ALTER TABLE pending_messages ADD COLUMN target_endpoint_network TEXT NOT NULL DEFAULT ''"
            )
        if "target_endpoint_address" not in columns:
            connection.execute(
                "ALTER TABLE pending_messages ADD COLUMN target_endpoint_address TEXT NOT NULL DEFAULT ''"
            )

    def upsert_user_presence(
        self,
        *,
        user_id: str,
        last_seen_station_id: str,
        last_seen_via_network: str,
        last_seen_at: str | None = None,
        last_seen_endpoint_id: str = "",
        presence_state: str = "registered",
        presence_evidence: str = "message",
        updated_at: str | None = None,
    ) -> UserPresenceRecord:
        record = UserPresenceRecord(
            user_id=str(user_id).strip(),
            last_seen_at=str(last_seen_at or _utc_now_iso()).strip(),
            last_seen_station_id=str(last_seen_station_id).strip(),
            last_seen_via_network=str(last_seen_via_network).strip(),
            last_seen_endpoint_id=str(last_seen_endpoint_id).strip(),
            presence_state=str(presence_state).strip(),
            presence_evidence=str(presence_evidence).strip(),
            updated_at=str(updated_at or _utc_now_iso()).strip(),
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO user_presence (
                    user_id, last_seen_at, last_seen_station_id, last_seen_via_network,
                    last_seen_endpoint_id, presence_state, presence_evidence, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    last_seen_station_id = excluded.last_seen_station_id,
                    last_seen_via_network = excluded.last_seen_via_network,
                    last_seen_endpoint_id = excluded.last_seen_endpoint_id,
                    presence_state = excluded.presence_state,
                    presence_evidence = excluded.presence_evidence,
                    updated_at = excluded.updated_at
                """,
                (
                    record.user_id,
                    record.last_seen_at,
                    record.last_seen_station_id,
                    record.last_seen_via_network,
                    record.last_seen_endpoint_id,
                    record.presence_state,
                    record.presence_evidence,
                    record.updated_at,
                ),
            )
            connection.commit()
        return record

    def get_user_presence(self, user_id: str) -> UserPresenceRecord | None:
        normalized_user_id = str(user_id).strip()
        if not normalized_user_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT user_id, last_seen_at, last_seen_station_id, last_seen_via_network,
                       last_seen_endpoint_id, presence_state, presence_evidence, updated_at
                FROM user_presence
                WHERE user_id = ?
                """,
                (normalized_user_id,),
            ).fetchone()
        return UserPresenceRecord(*row) if row is not None else None

    def upsert_delivery_policy(
        self,
        *,
        user_id: str,
        preferred_routes: list[str] | tuple[str, ...],
        fallback_email_enabled: bool = False,
        fallback_hold_seconds: int = 0,
        retry_interval_seconds: int = 300,
        max_attempts: int = 10,
        message_expiry_seconds: int = 86400,
        allow_group_fallback: bool = False,
        updated_at: str | None = None,
    ) -> DeliveryPolicyRecord:
        record = DeliveryPolicyRecord(
            user_id=str(user_id).strip(),
            preferred_routes=tuple(str(route).strip() for route in preferred_routes if str(route).strip()),
            fallback_email_enabled=bool(fallback_email_enabled),
            fallback_hold_seconds=int(fallback_hold_seconds),
            retry_interval_seconds=int(retry_interval_seconds),
            max_attempts=int(max_attempts),
            message_expiry_seconds=int(message_expiry_seconds),
            allow_group_fallback=bool(allow_group_fallback),
            updated_at=str(updated_at or _utc_now_iso()).strip(),
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO delivery_policies (
                    user_id, preferred_route_json, fallback_email_enabled, fallback_hold_seconds,
                    retry_interval_seconds, max_attempts, message_expiry_seconds,
                    allow_group_fallback, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferred_route_json = excluded.preferred_route_json,
                    fallback_email_enabled = excluded.fallback_email_enabled,
                    fallback_hold_seconds = excluded.fallback_hold_seconds,
                    retry_interval_seconds = excluded.retry_interval_seconds,
                    max_attempts = excluded.max_attempts,
                    message_expiry_seconds = excluded.message_expiry_seconds,
                    allow_group_fallback = excluded.allow_group_fallback,
                    updated_at = excluded.updated_at
                """,
                (
                    record.user_id,
                    json.dumps(list(record.preferred_routes)),
                    1 if record.fallback_email_enabled else 0,
                    record.fallback_hold_seconds,
                    record.retry_interval_seconds,
                    record.max_attempts,
                    record.message_expiry_seconds,
                    1 if record.allow_group_fallback else 0,
                    record.updated_at,
                ),
            )
            connection.commit()
        return record

    def get_delivery_policy(self, user_id: str) -> DeliveryPolicyRecord | None:
        normalized_user_id = str(user_id).strip()
        if not normalized_user_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT user_id, preferred_route_json, fallback_email_enabled, fallback_hold_seconds,
                       retry_interval_seconds, max_attempts, message_expiry_seconds,
                       allow_group_fallback, updated_at
                FROM delivery_policies
                WHERE user_id = ?
                """,
                (normalized_user_id,),
            ).fetchone()
        if row is None:
            return None
        routes = tuple(
            str(route).strip()
            for route in json.loads(str(row[1] or "[]"))
            if str(route).strip()
        )
        return DeliveryPolicyRecord(
            user_id=str(row[0]),
            preferred_routes=routes,
            fallback_email_enabled=bool(row[2]),
            fallback_hold_seconds=int(row[3]),
            retry_interval_seconds=int(row[4]),
            max_attempts=int(row[5]),
            message_expiry_seconds=int(row[6]),
            allow_group_fallback=bool(row[7]),
            updated_at=str(row[8]),
        )

    def save_reply_template(
        self,
        *,
        template_key: str,
        language_code: str,
        template_text: str,
        version: int = 1,
        updated_at: str | None = None,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO reply_templates (template_key, language_code, template_text, version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(template_key, language_code) DO UPDATE SET
                    template_text = excluded.template_text,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    str(template_key).strip(),
                    str(language_code).strip(),
                    str(template_text),
                    int(version),
                    str(updated_at or _utc_now_iso()).strip(),
                ),
            )
            connection.commit()

    def get_reply_template(
        self,
        *,
        template_key: str,
        language_code: str,
        fallback_language: str | None = None,
    ) -> str | None:
        normalized_key = str(template_key).strip()
        normalized_language = str(language_code).strip().lower()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT template_text
                FROM reply_templates
                WHERE template_key = ? AND language_code = ?
                """,
                (normalized_key, normalized_language),
            ).fetchone()
            if row is not None:
                return str(row[0])
            if fallback_language:
                normalized_fallback = str(fallback_language).strip().lower()
                if normalized_fallback and normalized_fallback != normalized_language:
                    row = connection.execute(
                        """
                        SELECT template_text
                        FROM reply_templates
                        WHERE template_key = ? AND language_code = ?
                        """,
                        (normalized_key, normalized_fallback),
                    ).fetchone()
                    if row is not None:
                        return str(row[0])
        return None

    def upsert_sink(
        self,
        *,
        sink_id: str,
        display_name: str = "",
        owner_ref: str = "",
        enabled: bool = True,
        accepted_content_classes: tuple[str, ...] | list[str] = ("telemetry", "message"),
        accepted_actor_classes: tuple[str, ...] | list[str] = ("device", "person"),
        max_body_chars: int = 1000,
        notes: str = "",
        updated_at: str | None = None,
    ) -> SinkRecord:
        record = SinkRecord(
            sink_id=str(sink_id).strip().lower(),
            display_name=str(display_name).strip(),
            owner_ref=str(owner_ref).strip(),
            enabled=bool(enabled),
            accepted_content_classes=tuple(str(item).strip().lower() for item in accepted_content_classes if str(item).strip()),
            accepted_actor_classes=tuple(str(item).strip().lower() for item in accepted_actor_classes if str(item).strip()),
            max_body_chars=max(1, int(max_body_chars)),
            notes=str(notes).strip(),
            updated_at=str(updated_at or _utc_now_iso()).strip(),
        )
        if not record.sink_id:
            raise ValueError("sink_id is required")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO sinks (
                    sink_id, display_name, owner_ref, enabled, accepted_content_classes_json,
                    accepted_actor_classes_json, max_body_chars, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sink_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    owner_ref = excluded.owner_ref,
                    enabled = excluded.enabled,
                    accepted_content_classes_json = excluded.accepted_content_classes_json,
                    accepted_actor_classes_json = excluded.accepted_actor_classes_json,
                    max_body_chars = excluded.max_body_chars,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    record.sink_id,
                    record.display_name,
                    record.owner_ref,
                    1 if record.enabled else 0,
                    json.dumps(list(record.accepted_content_classes)),
                    json.dumps(list(record.accepted_actor_classes)),
                    record.max_body_chars,
                    record.notes,
                    record.updated_at,
                ),
            )
            connection.commit()
        return record

    def get_sink(self, sink_id: str) -> SinkRecord | None:
        normalized = str(sink_id or "").strip().lower()
        if not normalized:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT sink_id, display_name, owner_ref, enabled, accepted_content_classes_json,
                       accepted_actor_classes_json, max_body_chars, notes, updated_at
                FROM sinks
                WHERE sink_id = ?
                """,
                (normalized,),
            ).fetchone()
        return self._row_to_sink(row) if row is not None else None

    def list_sinks(self) -> list[SinkRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT sink_id, display_name, owner_ref, enabled, accepted_content_classes_json,
                       accepted_actor_classes_json, max_body_chars, notes, updated_at
                FROM sinks
                ORDER BY sink_id
                """
            ).fetchall()
        return [self._row_to_sink(row) for row in rows]

    def upsert_sink_endpoint(
        self,
        *,
        sink_id: str,
        adapter_type: str,
        config: dict[str, object] | None = None,
        sink_endpoint_id: str | None = None,
        enabled: bool = True,
        priority: int = 100,
        updated_at: str | None = None,
    ) -> SinkEndpointRecord:
        record = SinkEndpointRecord(
            sink_endpoint_id=str(sink_endpoint_id or "").strip() or _new_record_id("sinkep"),
            sink_id=str(sink_id).strip().lower(),
            adapter_type=str(adapter_type).strip().lower(),
            config=dict(config or {}),
            enabled=bool(enabled),
            priority=int(priority),
            last_delivered_at=None,
            updated_at=str(updated_at or _utc_now_iso()).strip(),
        )
        if not record.sink_id or not record.adapter_type:
            raise ValueError("sink_id and adapter_type are required")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO sink_endpoints (
                    sink_endpoint_id, sink_id, adapter_type, config_json, enabled, priority, last_delivered_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(sink_endpoint_id) DO UPDATE SET
                    sink_id = excluded.sink_id,
                    adapter_type = excluded.adapter_type,
                    config_json = excluded.config_json,
                    enabled = excluded.enabled,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (
                    record.sink_endpoint_id,
                    record.sink_id,
                    record.adapter_type,
                    json.dumps(record.config, sort_keys=True),
                    1 if record.enabled else 0,
                    record.priority,
                    record.updated_at,
                ),
            )
            connection.commit()
        return record

    def get_sink_endpoint(self, sink_endpoint_id: str) -> SinkEndpointRecord | None:
        normalized = str(sink_endpoint_id or "").strip()
        if not normalized:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT sink_endpoint_id, sink_id, adapter_type, config_json, enabled, priority, last_delivered_at, updated_at
                FROM sink_endpoints
                WHERE sink_endpoint_id = ?
                """,
                (normalized,),
            ).fetchone()
        return self._row_to_sink_endpoint(row) if row is not None else None

    def list_sink_endpoints(self, sink_id: str | None = None, *, enabled_only: bool = False) -> list[SinkEndpointRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if sink_id is not None:
            clauses.append("sink_id = ?")
            params.append(str(sink_id).strip().lower())
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT sink_endpoint_id, sink_id, adapter_type, config_json, enabled, priority, last_delivered_at, updated_at
                FROM sink_endpoints
                {where}
                ORDER BY sink_id, priority DESC, sink_endpoint_id
                """,
                params,
            ).fetchall()
        return [self._row_to_sink_endpoint(row) for row in rows]

    def mark_sink_endpoint_delivered(self, sink_endpoint_id: str, *, delivered_at: str | None = None) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE sink_endpoints SET last_delivered_at = ? WHERE sink_endpoint_id = ?",
                (str(delivered_at or _utc_now_iso()).strip(), str(sink_endpoint_id).strip()),
            )
            connection.commit()

    def _row_to_sink(self, row: tuple) -> SinkRecord:
        return SinkRecord(
            sink_id=str(row[0]),
            display_name=str(row[1] or ""),
            owner_ref=str(row[2] or ""),
            enabled=bool(row[3]),
            accepted_content_classes=tuple(str(item) for item in json.loads(str(row[4] or "[]"))),
            accepted_actor_classes=tuple(str(item) for item in json.loads(str(row[5] or "[]"))),
            max_body_chars=int(row[6]),
            notes=str(row[7] or ""),
            updated_at=str(row[8]),
        )

    def _row_to_sink_endpoint(self, row: tuple) -> SinkEndpointRecord:
        config = json.loads(str(row[3] or "{}"))
        return SinkEndpointRecord(
            sink_endpoint_id=str(row[0]),
            sink_id=str(row[1]),
            adapter_type=str(row[2]),
            config=dict(config) if isinstance(config, dict) else {},
            enabled=bool(row[4]),
            priority=int(row[5]),
            last_delivered_at=_normalize_iso(row[6]),
            updated_at=str(row[7]),
        )

    def enqueue_message(
        self,
        *,
        sender_user_id: str,
        target_user_id: str,
        target_endpoint_network: str = "",
        target_endpoint_address: str = "",
        conversation_scope: str,
        message_class: str,
        plaintext_body: str,
        accepted_station_id: str,
        owning_station_id: str,
        body_summary: str = "",
        selected_route: str = "",
        fallback_route: str = "",
        next_attempt_after: str | None = None,
        fallback_after: str | None = None,
        expires_at: str | None = None,
        created_by_event_id: str = "",
    ) -> PendingMessageRecord:
        created_at = _utc_now_iso()
        record = PendingMessageRecord(
            message_id=_new_record_id("msg"),
            sender_user_id=str(sender_user_id).strip(),
            target_user_id=str(target_user_id).strip(),
            target_endpoint_network=str(target_endpoint_network).strip().lower(),
            target_endpoint_address=str(target_endpoint_address).strip(),
            conversation_scope=str(conversation_scope).strip(),
            message_class=str(message_class).strip(),
            plaintext_body=str(plaintext_body),
            body_summary=str(body_summary),
            delivery_state="waiting_for_path",
            selected_route=str(selected_route).strip(),
            fallback_route=str(fallback_route).strip(),
            created_at=created_at,
            next_attempt_after=_normalize_iso(next_attempt_after),
            fallback_after=_normalize_iso(fallback_after),
            expires_at=_normalize_iso(expires_at),
            last_attempt_at=None,
            last_error_code="",
            accepted_station_id=str(accepted_station_id).strip(),
            owning_station_id=str(owning_station_id).strip(),
            claim_token="",
            claim_station_id="",
            claim_expires_at=None,
            claim_heartbeat_at=None,
            created_by_event_id=str(created_by_event_id).strip(),
            updated_at=created_at,
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO pending_messages (
                    message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                    conversation_scope, message_class,
                    plaintext_body, body_summary, delivery_state, selected_route, fallback_route,
                    created_at, next_attempt_after, fallback_after, expires_at, last_attempt_at,
                    last_error_code, accepted_station_id, owning_station_id, claim_token,
                    claim_station_id, claim_expires_at, claim_heartbeat_at, created_by_event_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._record_to_row(record),
            )
            connection.commit()
        return record

    def get_pending_message(self, message_id: str) -> PendingMessageRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
              SELECT message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                  conversation_scope, message_class,
                       plaintext_body, body_summary, delivery_state, selected_route, fallback_route,
                       created_at, next_attempt_after, fallback_after, expires_at, last_attempt_at,
                       last_error_code, accepted_station_id, owning_station_id, claim_token,
                       claim_station_id, claim_expires_at, claim_heartbeat_at, created_by_event_id, updated_at
                FROM pending_messages
                WHERE message_id = ?
                """,
                (str(message_id).strip(),),
            ).fetchone()
        return self._row_to_pending_message(row) if row is not None else None

    def list_pending_messages(self, *, include_content: bool = False) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
              SELECT message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                  conversation_scope, message_class,
                       plaintext_body, body_summary, delivery_state, selected_route, fallback_route,
                       created_at, next_attempt_after, fallback_after, expires_at, last_attempt_at,
                       last_error_code, accepted_station_id, owning_station_id, claim_token,
                       claim_station_id, claim_expires_at, claim_heartbeat_at, created_by_event_id, updated_at
                FROM pending_messages
                ORDER BY created_at DESC, message_id DESC
                """
            ).fetchall()
        return [self._row_to_pending_message(row).as_dict(include_content=include_content) for row in rows]

    def list_pending_messages_for_route(
        self,
        *,
        route_name: str,
        include_content: bool = False,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        normalized_route = str(route_name).strip().lower()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                       conversation_scope, message_class, plaintext_body, body_summary, delivery_state,
                       selected_route, fallback_route, created_at, next_attempt_after, fallback_after,
                       expires_at, last_attempt_at, last_error_code, accepted_station_id, owning_station_id,
                       claim_token, claim_station_id, claim_expires_at, claim_heartbeat_at,
                       created_by_event_id, updated_at
                FROM pending_messages
                WHERE lower(selected_route) = ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?
                """,
                (normalized_route, max(1, int(limit))),
            ).fetchall()
        return [self._row_to_pending_message(row).as_dict(include_content=include_content) for row in rows]

    def claim_messages_for_route(
        self,
        *,
        route_name: str,
        station_id: str,
        limit: int = 10,
        now: str | None = None,
        claim_window_seconds: int = 60,
    ) -> list[PendingMessageRecord]:
        normalized_route = str(route_name).strip().lower()
        now_text = _normalize_iso(now) or _utc_now_iso()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                       conversation_scope, message_class, plaintext_body, body_summary, delivery_state,
                       selected_route, fallback_route, created_at, next_attempt_after, fallback_after,
                       expires_at, last_attempt_at, last_error_code, accepted_station_id, owning_station_id,
                       claim_token, claim_station_id, claim_expires_at, claim_heartbeat_at,
                       created_by_event_id, updated_at
                FROM pending_messages
                WHERE lower(selected_route) = ?
                  AND target_endpoint_address != ''
                  AND delivery_state = 'waiting_for_path'
                  AND (next_attempt_after IS NULL OR next_attempt_after = '' OR next_attempt_after <= ?)
                ORDER BY created_at ASC, message_id ASC
                LIMIT ?
                """,
                (normalized_route, now_text, max(1, int(limit))),
            ).fetchall()
        claimed: list[PendingMessageRecord] = []
        for row in rows:
            record = self._row_to_pending_message(row)
            claimed_record = self.claim_message(
                message_id=record.message_id,
                station_id=station_id,
                claim_window_seconds=claim_window_seconds,
                now=now_text,
            )
            if claimed_record is not None:
                claimed.append(claimed_record)
        return claimed

    def refresh_waiting_messages_for_user(
        self,
        *,
        target_user_id: str,
        route_name: str,
        endpoint_network: str,
        endpoint_address: str,
        target_identity_aliases: tuple[str, ...] = (),
        now: str | None = None,
    ) -> list[PendingMessageRecord]:
        normalized_user_id = str(target_user_id).strip()
        normalized_route = str(route_name).strip().lower()
        normalized_network = str(endpoint_network).strip().lower()
        normalized_address = str(endpoint_address).strip()
        if not normalized_user_id or not normalized_route or not normalized_address:
            return []

        aliases = tuple(dict.fromkeys(
            alias.casefold()
            for alias in (str(value or "").strip() for value in target_identity_aliases)
            if alias and alias.casefold() != normalized_user_id.casefold()
        ))
        target_filter = "target_user_id = ?"
        params: list[object] = [normalized_user_id]
        if aliases:
            target_filter += f" OR lower(target_user_id) IN ({', '.join('?' for _ in aliases)})"
            params.extend(aliases)

        with closing(self._connect()) as connection:
                 rows = connection.execute(
                  f"""
                  SELECT message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                      conversation_scope, message_class, plaintext_body, body_summary, delivery_state,
                      selected_route, fallback_route, created_at, next_attempt_after, fallback_after,
                      expires_at, last_attempt_at, last_error_code, accepted_station_id, owning_station_id,
                      claim_token, claim_station_id, claim_expires_at, claim_heartbeat_at,
                      created_by_event_id, updated_at
                  FROM pending_messages
                  WHERE ({target_filter})
                    AND delivery_state = 'waiting_for_path'
                  ORDER BY created_at ASC, message_id ASC
                  """,
                  tuple(params),
                 ).fetchall()

        refreshed: list[PendingMessageRecord] = []
        updated_at = _normalize_iso(now) or _utc_now_iso()
        for row in rows:
            current = self._row_to_pending_message(row)
            refreshed_record = replace(
                current,
                target_user_id=normalized_user_id,
                target_endpoint_network=normalized_network,
                target_endpoint_address=normalized_address,
                selected_route=normalized_route,
                next_attempt_after=None,
                updated_at=updated_at,
            )
            self._update_pending_message(refreshed_record)
            refreshed.append(refreshed_record)
        return refreshed

    def list_delivery_attempts(self, *, message_id: str | None = None) -> list[dict[str, object]]:
        query = """
            SELECT attempt_id, message_id, claim_token, attempted_at, station_id,
                   route_name, outcome, outcome_detail, gateway_request_id, updated_at
            FROM delivery_attempts
        """
        params: tuple[object, ...] = ()
        if message_id:
            query += " WHERE message_id = ?"
            params = (str(message_id).strip(),)
        query += " ORDER BY attempted_at DESC, attempt_id DESC"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_delivery_attempt(row).as_dict() for row in rows]

    def claim_message(
        self,
        *,
        message_id: str,
        station_id: str,
        claim_window_seconds: int = 60,
        now: str | None = None,
    ) -> PendingMessageRecord | None:
        current = self.get_pending_message(message_id)
        if current is None or current.delivery_state in self.TERMINAL_STATES:
            return None

        now_dt = _parse_iso(now) or _utc_now()
        if current.claim_station_id:
            expires_at = _parse_iso(current.claim_expires_at)
            if expires_at is not None and expires_at > now_dt:
                return None

        claimed = replace(
            current,
            delivery_state="attempting",
            claim_token=_new_record_id("claim"),
            claim_station_id=str(station_id).strip(),
            claim_expires_at=(now_dt + timedelta(seconds=max(1, int(claim_window_seconds)))).isoformat(),
            claim_heartbeat_at=now_dt.isoformat(),
            updated_at=now_dt.isoformat(),
        )
        self._update_pending_message(claimed)
        return claimed

    def renew_message_claim(
        self,
        *,
        message_id: str,
        station_id: str,
        claim_token: str,
        claim_window_seconds: int = 60,
        now: str | None = None,
    ) -> PendingMessageRecord | None:
        current = self.get_pending_message(message_id)
        if current is None:
            return None
        if current.claim_station_id != str(station_id).strip() or current.claim_token != str(claim_token).strip():
            return None

        now_dt = _parse_iso(now) or _utc_now()
        renewed = replace(
            current,
            claim_expires_at=(now_dt + timedelta(seconds=max(1, int(claim_window_seconds)))).isoformat(),
            claim_heartbeat_at=now_dt.isoformat(),
            updated_at=now_dt.isoformat(),
        )
        self._update_pending_message(renewed)
        return renewed

    def complete_message(
        self,
        *,
        message_id: str,
        station_id: str,
        claim_token: str,
        outcome: str,
        route_name: str,
        gateway_request_id: str = "",
        outcome_detail: str = "",
        now: str | None = None,
    ) -> PendingMessageRecord | None:
        current = self.get_pending_message(message_id)
        if current is None:
            return None
        if current.claim_station_id != str(station_id).strip() or current.claim_token != str(claim_token).strip():
            return None

        completed_at = _parse_iso(now) or _utc_now()
        self._insert_delivery_attempt(
            message_id=current.message_id,
            claim_token=current.claim_token,
            station_id=str(station_id).strip(),
            route_name=str(route_name).strip(),
            outcome=str(outcome).strip(),
            outcome_detail=str(outcome_detail),
            gateway_request_id=str(gateway_request_id).strip(),
            attempted_at=completed_at.isoformat(),
        )
        completed = replace(
            current,
            delivery_state=str(outcome).strip(),
            selected_route=str(route_name).strip(),
            last_attempt_at=completed_at.isoformat(),
            last_error_code="",
            claim_token="",
            claim_station_id="",
            claim_expires_at=None,
            claim_heartbeat_at=None,
            updated_at=completed_at.isoformat(),
        )
        self._update_pending_message(completed)
        return completed

    def release_message_claim(
        self,
        *,
        message_id: str,
        station_id: str,
        claim_token: str,
        route_name: str,
        next_attempt_after: str | None = None,
        outcome: str = "retryable_failure",
        outcome_detail: str = "",
        gateway_request_id: str = "",
        now: str | None = None,
    ) -> PendingMessageRecord | None:
        current = self.get_pending_message(message_id)
        if current is None:
            return None
        if current.claim_station_id != str(station_id).strip() or current.claim_token != str(claim_token).strip():
            return None

        released_at = _parse_iso(now) or _utc_now()
        self._insert_delivery_attempt(
            message_id=current.message_id,
            claim_token=current.claim_token,
            station_id=str(station_id).strip(),
            route_name=str(route_name).strip(),
            outcome=str(outcome).strip(),
            outcome_detail=str(outcome_detail),
            gateway_request_id=str(gateway_request_id).strip(),
            attempted_at=released_at.isoformat(),
        )
        released = replace(
            current,
            delivery_state="waiting_for_path",
            selected_route=str(route_name).strip(),
            last_attempt_at=released_at.isoformat(),
            last_error_code=str(outcome).strip(),
            next_attempt_after=_normalize_iso(next_attempt_after),
            claim_token="",
            claim_station_id="",
            claim_expires_at=None,
            claim_heartbeat_at=None,
            updated_at=released_at.isoformat(),
        )
        self._update_pending_message(released)
        return released

    def list_open_messages(self) -> list[PendingMessageRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT message_id, sender_user_id, target_user_id, target_endpoint_network, target_endpoint_address,
                       conversation_scope, message_class, plaintext_body, body_summary, delivery_state,
                       selected_route, fallback_route, created_at, next_attempt_after, fallback_after,
                       expires_at, last_attempt_at, last_error_code, accepted_station_id, owning_station_id,
                       claim_token, claim_station_id, claim_expires_at, claim_heartbeat_at,
                       created_by_event_id, updated_at
                FROM pending_messages
                WHERE delivery_state NOT IN ('delivered', 'fallback_sent', 'expired', 'cancelled', 'failed')
                ORDER BY created_at ASC, message_id ASC
                """
            ).fetchall()
        return [self._row_to_pending_message(row) for row in rows]

    def count_delivery_attempts(self, message_id: str) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM delivery_attempts WHERE message_id = ?",
                (str(message_id).strip(),),
            ).fetchone()
        return int(row[0]) if row else 0

    def expire_message(
        self,
        *,
        message_id: str,
        station_id: str,
        reason: str,
        now: str | None = None,
    ) -> PendingMessageRecord | None:
        current = self.get_pending_message(message_id)
        if current is None or current.delivery_state in self.TERMINAL_STATES:
            return None
        expired_at = _parse_iso(now) or _utc_now()
        self._insert_delivery_attempt(
            message_id=current.message_id,
            claim_token=current.claim_token,
            station_id=str(station_id).strip(),
            route_name=current.selected_route,
            outcome="expired",
            outcome_detail=str(reason),
            gateway_request_id="",
            attempted_at=expired_at.isoformat(),
        )
        expired = replace(
            current,
            delivery_state="expired",
            last_error_code=str(reason).strip(),
            claim_token="",
            claim_station_id="",
            claim_expires_at=None,
            claim_heartbeat_at=None,
            updated_at=expired_at.isoformat(),
        )
        self._update_pending_message(expired)
        return expired

    def release_expired_claims(self, *, now: str | None = None) -> list[PendingMessageRecord]:
        now_dt = _parse_iso(now) or _utc_now()
        released: list[PendingMessageRecord] = []
        for record in self.list_open_messages():
            if not record.claim_station_id:
                continue
            claim_expires_at = _parse_iso(record.claim_expires_at)
            if claim_expires_at is not None and claim_expires_at > now_dt:
                continue
            reset = replace(
                record,
                delivery_state="waiting_for_path",
                last_error_code="claim_expired",
                claim_token="",
                claim_station_id="",
                claim_expires_at=None,
                claim_heartbeat_at=None,
                updated_at=now_dt.isoformat(),
            )
            self._update_pending_message(reset)
            released.append(reset)
        return released

    def _insert_delivery_attempt(
        self,
        *,
        message_id: str,
        claim_token: str,
        station_id: str,
        route_name: str,
        outcome: str,
        outcome_detail: str,
        gateway_request_id: str,
        attempted_at: str,
    ) -> None:
        updated_at = attempted_at or _utc_now_iso()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO delivery_attempts (
                    attempt_id, message_id, claim_token, attempted_at, station_id,
                    route_name, outcome, outcome_detail, gateway_request_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_record_id("attempt"),
                    message_id,
                    claim_token,
                    attempted_at,
                    station_id,
                    route_name,
                    outcome,
                    outcome_detail,
                    gateway_request_id,
                    updated_at,
                ),
            )
            connection.commit()

    def _update_pending_message(self, record: PendingMessageRecord) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE pending_messages
                SET sender_user_id = ?,
                    target_user_id = ?,
                    target_endpoint_network = ?,
                    target_endpoint_address = ?,
                    conversation_scope = ?,
                    message_class = ?,
                    plaintext_body = ?,
                    body_summary = ?,
                    delivery_state = ?,
                    selected_route = ?,
                    fallback_route = ?,
                    created_at = ?,
                    next_attempt_after = ?,
                    fallback_after = ?,
                    expires_at = ?,
                    last_attempt_at = ?,
                    last_error_code = ?,
                    accepted_station_id = ?,
                    owning_station_id = ?,
                    claim_token = ?,
                    claim_station_id = ?,
                    claim_expires_at = ?,
                    claim_heartbeat_at = ?,
                    created_by_event_id = ?,
                    updated_at = ?
                WHERE message_id = ?
                """,
                self._record_to_row(record)[1:] + (record.message_id,),
            )
            connection.commit()

    def _record_to_row(self, record: PendingMessageRecord) -> tuple[object, ...]:
        return (
            record.message_id,
            record.sender_user_id,
            record.target_user_id,
            record.target_endpoint_network,
            record.target_endpoint_address,
            record.conversation_scope,
            record.message_class,
            record.plaintext_body,
            record.body_summary,
            record.delivery_state,
            record.selected_route,
            record.fallback_route,
            record.created_at,
            record.next_attempt_after,
            record.fallback_after,
            record.expires_at,
            record.last_attempt_at,
            record.last_error_code,
            record.accepted_station_id,
            record.owning_station_id,
            record.claim_token,
            record.claim_station_id,
            record.claim_expires_at,
            record.claim_heartbeat_at,
            record.created_by_event_id,
            record.updated_at,
        )

    def _row_to_pending_message(self, row: sqlite3.Row | tuple[object, ...]) -> PendingMessageRecord:
        return PendingMessageRecord(*row)

    def _row_to_delivery_attempt(self, row: sqlite3.Row | tuple[object, ...]) -> DeliveryAttemptRecord:
        return DeliveryAttemptRecord(*row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10.0)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection