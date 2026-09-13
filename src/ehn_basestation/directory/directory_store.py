from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_record_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _normalize_bool(value: bool) -> int:
    return 1 if value else 0


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    display_name: str
    status: str
    home_region: str
    is_local_to_station: bool
    retention_class: str
    promotion_status: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "status": self.status,
            "home_region": self.home_region,
            "is_local_to_station": str(self.is_local_to_station),
            "retention_class": self.retention_class,
            "promotion_status": self.promotion_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    identity_id: str
    user_id: str
    identity_type: str
    display_name: str
    messaging_mode: str
    policy_id: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class EndpointRecord:
    endpoint_id: str
    identity_id: str
    network: str
    native_address: str
    verified: bool
    enabled: bool
    priority: int
    trust_level: str
    replication_class: str
    last_verified_at: str | None
    capability_tags: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LocalUserEntry:
    user: UserRecord
    identity: IdentityRecord
    endpoints: tuple[EndpointRecord, ...]
    profile: dict[str, object] | None = None
    source_station: str | None = None
    conflict_flag: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "user": self.user.as_dict(),
            "identity": {
                "identity_id": self.identity.identity_id,
                "user_id": self.identity.user_id,
                "identity_type": self.identity.identity_type,
                "display_name": self.identity.display_name,
                "messaging_mode": self.identity.messaging_mode,
                "policy_id": str(self.identity.policy_id),
                "status": self.identity.status,
                "created_at": self.identity.created_at,
                "updated_at": self.identity.updated_at,
            },
            "endpoints": [
                {
                    "endpoint_id": endpoint.endpoint_id,
                    "identity_id": endpoint.identity_id,
                    "network": endpoint.network,
                    "native_address": endpoint.native_address,
                    "verified": str(endpoint.verified),
                    "enabled": str(endpoint.enabled),
                    "priority": endpoint.priority,
                    "trust_level": endpoint.trust_level,
                    "replication_class": endpoint.replication_class,
                    "last_verified_at": str(endpoint.last_verified_at),
                    "capability_tags": endpoint.capability_tags,
                    "updated_at": endpoint.updated_at,
                }
                for endpoint in self.endpoints
            ],
            "profile": dict(self.profile or {}),
            "source_station": self.source_station,
            "conflict_flag": str(self.conflict_flag),
        }


class DirectoryStore:
    def __init__(self, database_path: Path, *, origin_station_id: str) -> None:
        self._database_path = Path(database_path).resolve()
        self._origin_station_id = str(origin_station_id).strip()

    def initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    home_region TEXT NOT NULL,
                    is_local_to_station INTEGER NOT NULL,
                    retention_class TEXT NOT NULL,
                    promotion_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS identities (
                    identity_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    identity_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    messaging_mode TEXT NOT NULL,
                    policy_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS endpoints (
                    endpoint_id TEXT PRIMARY KEY,
                    identity_id TEXT NOT NULL,
                    network TEXT NOT NULL,
                    native_address TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    trust_level TEXT NOT NULL,
                    replication_class TEXT NOT NULL,
                    last_verified_at TEXT,
                    capability_tags TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(identity_id) REFERENCES identities(identity_id)
                );

                CREATE TABLE IF NOT EXISTS replication_state (
                    record_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    origin_station_id TEXT NOT NULL,
                    origin_version INTEGER NOT NULL,
                    last_modified_at TEXT NOT NULL,
                    sync_priority INTEGER NOT NULL,
                    replication_class TEXT NOT NULL,
                    conflict_flag INTEGER NOT NULL,
                    promotion_state TEXT NOT NULL,
                    PRIMARY KEY (record_type, record_id)
                );

                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    given_name TEXT NOT NULL,
                    family_name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone_primary TEXT NOT NULL,
                    preferred_language TEXT NOT NULL DEFAULT 'en',
                    home_location_text TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    member_status TEXT NOT NULL,
                    operator_notes TEXT NOT NULL,
                    teams_csv TEXT NOT NULL,
                    roles_csv TEXT NOT NULL,
                    active_group_member INTEGER NOT NULL,
                    last_active_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS identity_profiles (
                    identity_id TEXT PRIMARY KEY,
                    routing_notes TEXT NOT NULL,
                    rcs_name TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(identity_id) REFERENCES identities(identity_id)
                );

                CREATE TABLE IF NOT EXISTS endpoint_metadata (
                    endpoint_meta_id TEXT PRIMARY KEY,
                    endpoint_id TEXT NOT NULL,
                    meta_key TEXT NOT NULL,
                    meta_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(endpoint_id) REFERENCES endpoints(endpoint_id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    event_id TEXT PRIMARY KEY,
                    event_ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source_station_id TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )
            self._ensure_schema_columns(connection)
            connection.commit()

    def _ensure_schema_columns(self, connection: sqlite3.Connection) -> None:
        user_profile_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(user_profiles)").fetchall()
        }
        if "preferred_language" not in user_profile_columns:
            connection.execute(
                "ALTER TABLE user_profiles ADD COLUMN preferred_language TEXT NOT NULL DEFAULT 'en'"
            )

        identity_profile_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(identity_profiles)").fetchall()
        }
        if "rcs_name" not in identity_profile_columns:
            connection.execute(
                "ALTER TABLE identity_profiles ADD COLUMN rcs_name TEXT NOT NULL DEFAULT ''"
            )

    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id, event_ts, actor, action, subject_type, subject_id,
                       summary, source_station_id, details_json
                FROM audit_log
                ORDER BY event_ts DESC, event_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        events: list[dict[str, object]] = []
        for row in rows:
            raw_details = str(row[8] or "{}")
            try:
                details = json.loads(raw_details)
            except json.JSONDecodeError:
                details = {"raw": raw_details}
            events.append(
                {
                    "event_id": str(row[0]),
                    "event_ts": str(row[1]),
                    "actor": str(row[2]),
                    "action": str(row[3]),
                    "subject_type": str(row[4]),
                    "subject_id": str(row[5]),
                    "summary": str(row[6]),
                    "source_station_id": str(row[7]),
                    "details": details,
                }
            )
        return events

    def list_users(
        self,
        *,
        local_only: bool | None = None,
        promotion_status: str | None = None,
        retention_class: str | None = None,
        exclude_retention_classes: tuple[str, ...] = (),
    ) -> list[LocalUserEntry]:
        with closing(self._connect()) as connection:
            query = """
                SELECT
                    u.user_id,
                    u.display_name,
                    u.status,
                    u.home_region,
                    u.is_local_to_station,
                    u.retention_class,
                    u.promotion_status,
                    u.created_at,
                    u.updated_at,
                    i.identity_id,
                    i.identity_type,
                    i.display_name,
                    i.messaging_mode,
                    i.policy_id,
                    i.status,
                    i.created_at,
                    i.updated_at,
                    COALESCE(rs.origin_station_id, ?),
                    COALESCE(rs.conflict_flag, 0)
                FROM users u
                JOIN identities i ON i.identity_id = (
                    SELECT i2.identity_id
                    FROM identities i2
                    WHERE i2.user_id = u.user_id
                    ORDER BY i2.updated_at DESC, i2.identity_id DESC
                    LIMIT 1
                )
                LEFT JOIN replication_state rs ON rs.record_type = 'user' AND rs.record_id = u.user_id
                WHERE 1 = 1
            """
            params: list[object] = [self._origin_station_id]
            if local_only is not None:
                query += " AND u.is_local_to_station = ?"
                params.append(_normalize_bool(local_only))
            if promotion_status:
                query += " AND u.promotion_status = ?"
                params.append(str(promotion_status).strip())
            if retention_class:
                query += " AND u.retention_class = ?"
                params.append(str(retention_class).strip())
            for excluded_retention_class in exclude_retention_classes:
                query += " AND u.retention_class != ?"
                params.append(str(excluded_retention_class).strip())
            query += " ORDER BY u.display_name, u.user_id"
            rows = connection.execute(query, tuple(params)).fetchall()

            result = [self._entry_from_join_row(connection, row) for row in rows]
        return result

    def get_user_by_id(self, user_id: str) -> LocalUserEntry | None:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    u.user_id,
                    u.display_name,
                    u.status,
                    u.home_region,
                    u.is_local_to_station,
                    u.retention_class,
                    u.promotion_status,
                    u.created_at,
                    u.updated_at,
                    i.identity_id,
                    i.identity_type,
                    i.display_name,
                    i.messaging_mode,
                    i.policy_id,
                    i.status,
                    i.created_at,
                    i.updated_at,
                    COALESCE(rs.origin_station_id, ?),
                    COALESCE(rs.conflict_flag, 0)
                FROM users u
                JOIN identities i ON i.user_id = u.user_id
                LEFT JOIN replication_state rs ON rs.record_type = 'user' AND rs.record_id = u.user_id
                WHERE u.user_id = ?
                ORDER BY i.updated_at DESC
                LIMIT 1
                """,
                (self._origin_station_id, normalized_user_id),
            ).fetchone()
            if row is None:
                return None
            return self._entry_from_join_row(connection, row)

    def find_user_by_endpoint(self, *, network: str, native_address: str) -> LocalUserEntry | None:
        normalized_network = str(network or "").strip().upper()
        normalized_address = str(native_address or "").strip()
        if not normalized_network or not normalized_address:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    u.user_id,
                    u.display_name,
                    u.status,
                    u.home_region,
                    u.is_local_to_station,
                    u.retention_class,
                    u.promotion_status,
                    u.created_at,
                    u.updated_at,
                    i.identity_id,
                    i.identity_type,
                    i.display_name,
                    i.messaging_mode,
                    i.policy_id,
                    i.status,
                    i.created_at,
                    i.updated_at,
                    COALESCE(rs.origin_station_id, ?),
                    COALESCE(rs.conflict_flag, 0)
                FROM endpoints e
                JOIN identities i ON i.identity_id = e.identity_id
                JOIN users u ON u.user_id = i.user_id
                LEFT JOIN replication_state rs ON rs.record_type = 'user' AND rs.record_id = u.user_id
                WHERE e.network = ? AND e.native_address = ?
                ORDER BY e.priority DESC, i.updated_at DESC
                LIMIT 1
                """,
                (self._origin_station_id, normalized_network, normalized_address),
            ).fetchone()
            if row is None:
                return None
            return self._entry_from_join_row(connection, row)

    def find_user_by_rcs_name(self, rcs_name: str) -> LocalUserEntry | None:
        normalized_name = str(rcs_name or "").strip()
        if not normalized_name:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    u.user_id,
                    u.display_name,
                    u.status,
                    u.home_region,
                    u.is_local_to_station,
                    u.retention_class,
                    u.promotion_status,
                    u.created_at,
                    u.updated_at,
                    i.identity_id,
                    i.identity_type,
                    i.display_name,
                    i.messaging_mode,
                    i.policy_id,
                    i.status,
                    i.created_at,
                    i.updated_at,
                    COALESCE(rs.origin_station_id, ?),
                    COALESCE(rs.conflict_flag, 0)
                FROM identity_profiles ip
                JOIN identities i ON i.identity_id = ip.identity_id
                JOIN users u ON u.user_id = i.user_id
                LEFT JOIN replication_state rs ON rs.record_type = 'user' AND rs.record_id = u.user_id
                WHERE ip.rcs_name = ?
                ORDER BY i.updated_at DESC
                LIMIT 1
                """,
                (self._origin_station_id, normalized_name),
            ).fetchone()
            if row is None:
                return None
            return self._entry_from_join_row(connection, row)

    def find_user_by_identity_display_name(self, identity_display_name: str) -> LocalUserEntry | None:
        normalized_name = str(identity_display_name or "").strip()
        if not normalized_name:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    u.user_id,
                    u.display_name,
                    u.status,
                    u.home_region,
                    u.is_local_to_station,
                    u.retention_class,
                    u.promotion_status,
                    u.created_at,
                    u.updated_at,
                    i.identity_id,
                    i.identity_type,
                    i.display_name,
                    i.messaging_mode,
                    i.policy_id,
                    i.status,
                    i.created_at,
                    i.updated_at,
                    COALESCE(rs.origin_station_id, ?),
                    COALESCE(rs.conflict_flag, 0)
                FROM identities i
                JOIN users u ON u.user_id = i.user_id
                LEFT JOIN replication_state rs ON rs.record_type = 'user' AND rs.record_id = u.user_id
                WHERE i.display_name = ?
                ORDER BY i.updated_at DESC
                LIMIT 1
                """,
                (self._origin_station_id, normalized_name),
            ).fetchone()
            if row is None:
                return None
            return self._entry_from_join_row(connection, row)

    def save_directory_user(
        self,
        payload: dict[str, object],
        *,
        audit_actor: str = "system",
        audit_action: str = "save_directory_user",
    ) -> LocalUserEntry:
        now = _utc_now_iso()
        incoming_user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        incoming_identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        incoming_profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        incoming_endpoints = payload.get("endpoints") if isinstance(payload.get("endpoints"), list) else []

        user_id = str(incoming_user.get("user_id") or "").strip() or _new_record_id("user")
        requested_identity_id = str(incoming_identity.get("identity_id") or "").strip()
        display_name = str(incoming_user.get("display_name") or "").strip() or "Unnamed user"
        status = str(incoming_user.get("status") or incoming_profile.get("general_status") or "active").strip() or "active"
        home_region = str(incoming_user.get("home_region") or incoming_profile.get("home_region") or "").strip()
        is_local_to_station = bool(incoming_user.get("is_local_to_station") in {True, "True", "true", 1, "1"})
        retention_class = str(incoming_user.get("retention_class") or incoming_profile.get("retention_class") or "TRUSTED_STATION").strip() or "TRUSTED_STATION"
        promotion_status = str(incoming_user.get("promotion_status") or incoming_profile.get("promotion_status") or "PENDING_AUTHORITY_REVIEW").strip() or "PENDING_AUTHORITY_REVIEW"
        identity_display_name = str(incoming_identity.get("display_name") or incoming_profile.get("primary_identity_label") or display_name).strip() or display_name
        identity_type = str(incoming_identity.get("identity_type") or incoming_profile.get("identity_type") or "USER").strip() or "USER"
        messaging_mode = str(incoming_identity.get("messaging_mode") or incoming_profile.get("messaging_mode") or "BOTH").strip() or "BOTH"
        policy_id = str(incoming_identity.get("policy_id") or incoming_profile.get("policy_profile") or "").strip() or None

        with closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            created_at = str(existing[0]) if existing else now
            identity_id = requested_identity_id or self._find_latest_identity_id(connection, user_id) or _new_record_id("identity")

            connection.execute(
                """
                INSERT INTO users (
                    user_id, display_name, status, home_region, is_local_to_station,
                    retention_class, promotion_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    status = excluded.status,
                    home_region = excluded.home_region,
                    is_local_to_station = excluded.is_local_to_station,
                    retention_class = excluded.retention_class,
                    promotion_status = excluded.promotion_status,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    display_name,
                    status,
                    home_region,
                    _normalize_bool(is_local_to_station),
                    retention_class,
                    promotion_status,
                    created_at,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO identities (
                    identity_id, user_id, identity_type, display_name, messaging_mode,
                    policy_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    identity_type = excluded.identity_type,
                    display_name = excluded.display_name,
                    messaging_mode = excluded.messaging_mode,
                    policy_id = excluded.policy_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    identity_id,
                    user_id,
                    identity_type,
                    identity_display_name,
                    messaging_mode,
                    policy_id,
                    status,
                    created_at,
                    now,
                ),
            )
            self._save_profile_tables(connection, user_id=user_id, identity_id=identity_id, profile=incoming_profile, updated_at=now)
            self._replace_endpoints(connection, identity_id=identity_id, endpoints=incoming_endpoints, updated_at=now)
            self._upsert_replication_state(
                connection,
                record_type="user",
                record_id=user_id,
                last_modified_at=now,
                replication_class=retention_class,
                promotion_state=promotion_status,
            )
            self._upsert_replication_state(
                connection,
                record_type="identity",
                record_id=identity_id,
                last_modified_at=now,
                replication_class=retention_class,
                promotion_state=promotion_status,
            )
            self._record_audit_event(
                connection,
                actor=audit_actor,
                action=audit_action,
                subject_type="user",
                subject_id=user_id,
                summary=f"Saved directory user {display_name}.",
                details={
                    "identity_id": identity_id,
                    "promotion_status": promotion_status,
                    "retention_class": retention_class,
                    "endpoint_count": len(incoming_endpoints),
                    "is_local_to_station": is_local_to_station,
                },
            )
            connection.commit()

        reloaded = self.get_user_by_id(user_id)
        if reloaded is not None:
            return reloaded
        raise LookupError(f"Saved directory user {user_id} could not be reloaded.")

    def create_local_user(
        self,
        *,
        display_name: str,
        home_region: str = "",
        retention_class: str = "FIELD_OPERATIONAL",
        promotion_status: str = "LOCAL_ONLY",
        identity_display_name: str | None = None,
        identity_type: str = "USER",
        messaging_mode: str = "BOTH",
        endpoint_network: str | None = None,
        endpoint_address: str | None = None,
        endpoint_trust_level: str = "local",
        endpoint_verified: bool = False,
        audit_actor: str = "system",
    ) -> LocalUserEntry:
        now = _utc_now_iso()
        user = UserRecord(
            user_id=_new_record_id("user"),
            display_name=str(display_name).strip(),
            status="active",
            home_region=str(home_region).strip(),
            is_local_to_station=True,
            retention_class=str(retention_class).strip() or "FIELD_OPERATIONAL",
            promotion_status=str(promotion_status).strip() or "LOCAL_ONLY",
            created_at=now,
            updated_at=now,
        )
        identity = IdentityRecord(
            identity_id=_new_record_id("identity"),
            user_id=user.user_id,
            identity_type=str(identity_type).strip() or "USER",
            display_name=str(identity_display_name or display_name).strip(),
            messaging_mode=str(messaging_mode).strip() or "BOTH",
            policy_id=None,
            status="active",
            created_at=now,
            updated_at=now,
        )

        endpoints: list[EndpointRecord] = []
        normalized_network = str(endpoint_network or "").strip().upper()
        normalized_address = str(endpoint_address or "").strip()
        if normalized_network and normalized_address:
            endpoints.append(
                EndpointRecord(
                    endpoint_id=_new_record_id("endpoint"),
                    identity_id=identity.identity_id,
                    network=normalized_network,
                    native_address=normalized_address,
                    verified=bool(endpoint_verified),
                    enabled=True,
                    priority=100,
                    trust_level=str(endpoint_trust_level).strip() or "local",
                    replication_class="FIELD_OPERATIONAL",
                    last_verified_at=now if endpoint_verified else None,
                    capability_tags="",
                    updated_at=now,
                )
            )

        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO users (
                    user_id, display_name, status, home_region, is_local_to_station,
                    retention_class, promotion_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.user_id,
                    user.display_name,
                    user.status,
                    user.home_region,
                    _normalize_bool(user.is_local_to_station),
                    user.retention_class,
                    user.promotion_status,
                    user.created_at,
                    user.updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO identities (
                    identity_id, user_id, identity_type, display_name, messaging_mode,
                    policy_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.identity_id,
                    identity.user_id,
                    identity.identity_type,
                    identity.display_name,
                    identity.messaging_mode,
                    identity.policy_id,
                    identity.status,
                    identity.created_at,
                    identity.updated_at,
                ),
            )
            for endpoint in endpoints:
                connection.execute(
                    """
                    INSERT INTO endpoints (
                        endpoint_id, identity_id, network, native_address, verified,
                        enabled, priority, trust_level, replication_class,
                        last_verified_at, capability_tags, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        endpoint.endpoint_id,
                        endpoint.identity_id,
                        endpoint.network,
                        endpoint.native_address,
                        _normalize_bool(endpoint.verified),
                        _normalize_bool(endpoint.enabled),
                        endpoint.priority,
                        endpoint.trust_level,
                        endpoint.replication_class,
                        endpoint.last_verified_at,
                        endpoint.capability_tags,
                        endpoint.updated_at,
                    ),
                )

            self._upsert_replication_state(
                connection,
                record_type="user",
                record_id=user.user_id,
                last_modified_at=now,
                replication_class="FIELD_OPERATIONAL",
                promotion_state=user.promotion_status,
            )
            self._upsert_replication_state(
                connection,
                record_type="identity",
                record_id=identity.identity_id,
                last_modified_at=now,
                replication_class="FIELD_OPERATIONAL",
                promotion_state=user.promotion_status,
            )
            for endpoint in endpoints:
                self._upsert_replication_state(
                    connection,
                    record_type="endpoint",
                    record_id=endpoint.endpoint_id,
                    last_modified_at=now,
                    replication_class=endpoint.replication_class,
                    promotion_state=user.promotion_status,
                )
            self._record_audit_event(
                connection,
                actor=audit_actor,
                action="create_local_user",
                subject_type="user",
                subject_id=user.user_id,
                summary=f"Created local directory user {user.display_name}.",
                details={
                    "identity_id": identity.identity_id,
                    "promotion_status": user.promotion_status,
                    "endpoint_count": len(endpoints),
                },
            )
            connection.commit()

        return LocalUserEntry(user=user, identity=identity, endpoints=tuple(endpoints))

    def list_local_users(
        self,
        *,
        promotion_status: str | None = None,
        retention_class: str | None = None,
        exclude_retention_classes: tuple[str, ...] = (),
    ) -> list[LocalUserEntry]:
        return self.list_users(
            local_only=True,
            promotion_status=promotion_status,
            retention_class=retention_class,
            exclude_retention_classes=exclude_retention_classes,
        )

    def create_provisional_sender(
        self,
        *,
        display_name: str,
        network: str,
        native_address: str,
        actor_class: str,
        messaging_mode: str,
        capability_tags: str = "",
        gateway_id: str = "",
        notes: str = "",
    ) -> LocalUserEntry:
        # Provisional records use the canonical id as their identity label so operator views never show a placeholder name.
        user_id = _new_record_id("user")
        payload = {
            "user": {
                "user_id": user_id,
                "display_name": str(display_name).strip() or "Unknown sender",
                "status": "observed",
                "home_region": str(gateway_id).strip(),
                "is_local_to_station": True,
                "retention_class": "FIELD_OBSERVED",
                "promotion_status": "PENDING_AUTHORITY_REVIEW",
            },
            "identity": {
                "display_name": user_id,
                "identity_type": "DEVICE" if str(actor_class).strip().lower() == "device" else "USER",
                "messaging_mode": str(messaging_mode).strip() or "BOTH",
                "policy_id": "provisional-observed",
            },
            "profile": {
                "personal": {
                    "member_status": "Observed",
                    "home_location_text": str(gateway_id).strip(),
                },
                "identity": {
                    "routing_notes": str(notes).strip(),
                },
                "notes": {
                    "operator_notes": str(notes).strip(),
                },
            },
            "endpoints": [
                {
                    "network": str(network).strip().upper(),
                    "native_address": str(native_address).strip(),
                    "verified": False,
                    "enabled": True,
                    "priority": 100,
                    "trust_level": "provisional",
                    "replication_class": "FIELD_OBSERVED",
                    "capability_tags": str(capability_tags).strip(),
                    "metadata": {
                        "gateway_id": str(gateway_id).strip(),
                        "actor_class": str(actor_class).strip().lower() or "person",
                    },
                }
            ],
        }
        return self.save_directory_user(payload, audit_actor="router", audit_action="create_provisional_sender")

    def delete_directory_user(self, user_id: str, *, audit_actor: str = "system", audit_action: str = "delete_directory_user") -> bool:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return False

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT u.display_name
                FROM users u
                WHERE u.user_id = ?
                LIMIT 1
                """,
                (normalized_user_id,),
            ).fetchone()
            if row is None:
                return False

            display_name = str(row[0] or normalized_user_id)
            identity_rows = connection.execute(
                "SELECT identity_id FROM identities WHERE user_id = ? ORDER BY updated_at DESC, identity_id",
                (normalized_user_id,),
            ).fetchall()
            identity_ids = [str(identity_row[0]) for identity_row in identity_rows]

            endpoint_ids: list[str] = []
            if identity_ids:
                identity_placeholders = ", ".join("?" for _ in identity_ids)
                endpoint_rows = connection.execute(
                    f"SELECT endpoint_id FROM endpoints WHERE identity_id IN ({identity_placeholders})",
                    tuple(identity_ids),
                ).fetchall()
                endpoint_ids = [str(endpoint_row[0]) for endpoint_row in endpoint_rows]

            if endpoint_ids:
                endpoint_placeholders = ", ".join("?" for _ in endpoint_ids)
                connection.execute(
                    f"DELETE FROM endpoint_metadata WHERE endpoint_id IN ({endpoint_placeholders})",
                    tuple(endpoint_ids),
                )
                connection.execute(
                    f"DELETE FROM replication_state WHERE record_type = 'endpoint' AND record_id IN ({endpoint_placeholders})",
                    tuple(endpoint_ids),
                )

            if identity_ids:
                identity_placeholders = ", ".join("?" for _ in identity_ids)
                connection.execute(
                    f"DELETE FROM endpoints WHERE identity_id IN ({identity_placeholders})",
                    tuple(identity_ids),
                )
                connection.execute(
                    f"DELETE FROM identity_profiles WHERE identity_id IN ({identity_placeholders})",
                    tuple(identity_ids),
                )
                connection.execute(
                    f"DELETE FROM replication_state WHERE record_type = 'identity' AND record_id IN ({identity_placeholders})",
                    tuple(identity_ids),
                )
                connection.execute(
                    f"DELETE FROM identities WHERE identity_id IN ({identity_placeholders})",
                    tuple(identity_ids),
                )

            connection.execute("DELETE FROM user_profiles WHERE user_id = ?", (normalized_user_id,))
            connection.execute(
                "DELETE FROM replication_state WHERE record_type = 'user' AND record_id = ?",
                (normalized_user_id,),
            )
            connection.execute("DELETE FROM users WHERE user_id = ?", (normalized_user_id,))
            self._record_audit_event(
                connection,
                actor=audit_actor,
                action=audit_action,
                subject_type="user",
                subject_id=normalized_user_id,
                summary=f"Deleted directory user {display_name}.",
                details={
                    "identity_ids": identity_ids,
                    "identity_count": len(identity_ids),
                    "endpoint_count": len(endpoint_ids),
                    "display_name": display_name,
                },
            )
            connection.commit()
        return True

    def reset_directory_users(self, *, audit_actor: str = "system", include_audit: bool = False) -> dict[str, int]:
        with closing(self._connect()) as connection:
            counts = {
                "users": int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]),
                "identities": int(connection.execute("SELECT COUNT(*) FROM identities").fetchone()[0]),
                "endpoints": int(connection.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0]),
            }
            connection.execute("DELETE FROM endpoint_metadata")
            connection.execute("DELETE FROM endpoints")
            connection.execute("DELETE FROM identity_profiles")
            connection.execute("DELETE FROM identities")
            connection.execute("DELETE FROM user_profiles")
            connection.execute("DELETE FROM users")
            connection.execute("DELETE FROM replication_state")
            if include_audit:
                connection.execute("DELETE FROM audit_log")
            self._record_audit_event(
                connection,
                actor=audit_actor,
                action="reset_directory_users",
                subject_type="directory",
                subject_id=str(self._database_path),
                summary="Cleared directory user records.",
                details={**counts, "include_audit": include_audit},
            )
            connection.commit()
        return counts

    def seed_directory_users(self, rows: list[dict[str, object]], *, audit_actor: str = "system") -> list[LocalUserEntry]:
        saved: list[LocalUserEntry] = []
        for row in rows:
            saved.append(self.save_directory_user(row, audit_actor=audit_actor, audit_action="seed_directory_user"))
        return saved

    def _build_profile(
        self,
        connection: sqlite3.Connection,
        user: UserRecord,
        identity: IdentityRecord,
        endpoints: list[EndpointRecord],
    ) -> dict[str, object]:
        user_profile_row = connection.execute(
            """
             SELECT given_name, family_name, email, phone_primary, preferred_language, home_location_text,
                 latitude, longitude, member_status, operator_notes, teams_csv,
                   roles_csv, active_group_member, last_active_at
            FROM user_profiles
            WHERE user_id = ?
            """,
            (user.user_id,),
        ).fetchone()
        identity_profile_row = connection.execute(
            "SELECT routing_notes, rcs_name FROM identity_profiles WHERE identity_id = ?",
            (identity.identity_id,),
        ).fetchone()
        endpoint_meta_by_network: dict[str, dict[str, str]] = {}
        for endpoint in endpoints:
            meta_rows = connection.execute(
                "SELECT meta_key, meta_value FROM endpoint_metadata WHERE endpoint_id = ? ORDER BY meta_key",
                (endpoint.endpoint_id,),
            ).fetchall()
            endpoint_meta_by_network[endpoint.network] = {str(row[0]): str(row[1]) for row in meta_rows}

        teams = []
        roles = []
        active_group_member = False
        if user_profile_row:
            teams = [item for item in str(user_profile_row[10] or "").split("|") if item]
            roles = [item for item in str(user_profile_row[11] or "").split("|") if item]
            active_group_member = bool(user_profile_row[12])

        return {
            "personal": {
                "display_name": user.display_name,
                "given_name": str(user_profile_row[0]) if user_profile_row else "",
                "family_name": str(user_profile_row[1]) if user_profile_row else "",
                "email": str(user_profile_row[2]) if user_profile_row else "",
                "phone_primary": str(user_profile_row[3]) if user_profile_row else "",
                "preferred_language": str(user_profile_row[4] or "en") if user_profile_row else "en",
                "home_region": user.home_region,
                "home_location_text": str(user_profile_row[5]) if user_profile_row else "",
                "lat": user_profile_row[6] if user_profile_row else None,
                "lon": user_profile_row[7] if user_profile_row else None,
                "member_status": str(user_profile_row[8]) if user_profile_row else "",
                "general_status": user.status,
                "last_active_at": str(user_profile_row[13]) if user_profile_row and user_profile_row[13] else user.updated_at,
            },
            "identity": {
                "primary_identity_label": identity.display_name,
                "identity_type": identity.identity_type,
                "messaging_mode": identity.messaging_mode,
                "policy_profile": identity.policy_id or "",
                "routing_notes": str(identity_profile_row[0]) if identity_profile_row else "",
                "rcs_name": str(identity_profile_row[1]) if identity_profile_row else "",
            },
            "teams": {
                "teams": teams,
                "roles": roles,
                "active_group_member": active_group_member,
            },
            "presence": {
                "current_best_route": endpoints[0].network if endpoints else "",
                "last_seen_by_network": {endpoint.network: endpoint.updated_at or endpoint.last_verified_at or "" for endpoint in endpoints},
                "presence_state": "Configured" if endpoints else "No current observations",
            },
            "review": {
                "record_origin": self._origin_station_id,
                "replication_class": endpoints[0].replication_class if endpoints else user.retention_class,
                "promotion_status": user.promotion_status,
                "conflict_flag": False,
            },
            "notes": {
                "operator_notes": str(user_profile_row[9]) if user_profile_row else "",
            },
            "endpoint_metadata": endpoint_meta_by_network,
        }

    def _find_latest_identity_id(self, connection: sqlite3.Connection, user_id: str) -> str | None:
        row = connection.execute(
            """
            SELECT identity_id
            FROM identities
            WHERE user_id = ?
            ORDER BY updated_at DESC, identity_id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0] or "").strip() or None

    def _save_profile_tables(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: str,
        identity_id: str,
        profile: dict[str, object],
        updated_at: str,
    ) -> None:
        personal = profile.get("personal") if isinstance(profile.get("personal"), dict) else {}
        identity_profile = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
        teams = profile.get("teams") if isinstance(profile.get("teams"), dict) else {}
        notes = profile.get("notes") if isinstance(profile.get("notes"), dict) else {}

        connection.execute(
            """
            INSERT INTO user_profiles (
                user_id, given_name, family_name, email, phone_primary,
                preferred_language, home_location_text, latitude, longitude, member_status,
                operator_notes, teams_csv, roles_csv, active_group_member, last_active_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                given_name = excluded.given_name,
                family_name = excluded.family_name,
                email = excluded.email,
                phone_primary = excluded.phone_primary,
                preferred_language = excluded.preferred_language,
                home_location_text = excluded.home_location_text,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                member_status = excluded.member_status,
                operator_notes = excluded.operator_notes,
                teams_csv = excluded.teams_csv,
                roles_csv = excluded.roles_csv,
                active_group_member = excluded.active_group_member,
                last_active_at = excluded.last_active_at
            """,
            (
                user_id,
                str(personal.get("given_name") or "").strip(),
                str(personal.get("family_name") or "").strip(),
                str(personal.get("email") or "").strip(),
                str(personal.get("phone_primary") or "").strip(),
                str(personal.get("preferred_language") or "en").strip() or "en",
                str(personal.get("home_location_text") or "").strip(),
                personal.get("lat"),
                personal.get("lon"),
                str(personal.get("member_status") or "").strip(),
                str(notes.get("operator_notes") or "").strip(),
                "|".join(str(item).strip() for item in (teams.get("teams") or []) if str(item).strip()),
                "|".join(str(item).strip() for item in (teams.get("roles") or []) if str(item).strip()),
                _normalize_bool(bool(teams.get("active_group_member"))),
                str(personal.get("last_active_at") or updated_at),
            ),
        )
        connection.execute(
            """
            INSERT INTO identity_profiles (identity_id, routing_notes, rcs_name)
            VALUES (?, ?, ?)
            ON CONFLICT(identity_id) DO UPDATE SET
                routing_notes = excluded.routing_notes,
                rcs_name = excluded.rcs_name
            """,
            (
                identity_id,
                str(identity_profile.get("routing_notes") or "").strip(),
                str(identity_profile.get("rcs_name") or "").strip(),
            ),
        )

    def _replace_endpoints(
        self,
        connection: sqlite3.Connection,
        *,
        identity_id: str,
        endpoints: list[object],
        updated_at: str,
    ) -> None:
        existing_rows = connection.execute(
            "SELECT endpoint_id FROM endpoints WHERE identity_id = ?",
            (identity_id,),
        ).fetchall()
        existing_ids = [str(row[0]) for row in existing_rows]
        if existing_ids:
            placeholders = ", ".join("?" for _ in existing_ids)
            connection.execute(
                f"DELETE FROM endpoint_metadata WHERE endpoint_id IN ({placeholders})",
                tuple(existing_ids),
            )
        connection.execute("DELETE FROM endpoints WHERE identity_id = ?", (identity_id,))

        for raw_endpoint in endpoints:
            if not isinstance(raw_endpoint, dict):
                continue
            network = str(raw_endpoint.get("network") or "").strip().upper()
            native_address = str(raw_endpoint.get("native_address") or "").strip()
            if not network or not native_address:
                continue
            endpoint_id = str(raw_endpoint.get("endpoint_id") or "").strip() or _new_record_id("endpoint")
            verified = raw_endpoint.get("verified") in {True, "True", "true", 1, "1", "Yes", "yes"}
            enabled = raw_endpoint.get("enabled") not in {False, "False", "false", 0, "0", "No", "no"}
            priority = int(raw_endpoint.get("priority") or 100)
            trust_level = str(raw_endpoint.get("trust_level") or "draft").strip() or "draft"
            replication_class = str(raw_endpoint.get("replication_class") or "TRUSTED_STATION").strip() or "TRUSTED_STATION"
            last_verified_at = str(raw_endpoint.get("last_verified_at") or "").strip() or None
            capability_tags = str(raw_endpoint.get("capability_tags") or "").strip()
            connection.execute(
                """
                INSERT INTO endpoints (
                    endpoint_id, identity_id, network, native_address, verified,
                    enabled, priority, trust_level, replication_class,
                    last_verified_at, capability_tags, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint_id,
                    identity_id,
                    network,
                    native_address,
                    _normalize_bool(verified),
                    _normalize_bool(enabled),
                    priority,
                    trust_level,
                    replication_class,
                    last_verified_at,
                    capability_tags,
                    updated_at,
                ),
            )
            self._upsert_replication_state(
                connection,
                record_type="endpoint",
                record_id=endpoint_id,
                last_modified_at=updated_at,
                replication_class=replication_class,
                promotion_state="ACTIVE",
            )

            metadata = raw_endpoint.get("metadata") if isinstance(raw_endpoint.get("metadata"), dict) else {}
            for meta_key, meta_value in metadata.items():
                connection.execute(
                    """
                    INSERT INTO endpoint_metadata (
                        endpoint_meta_id, endpoint_id, meta_key, meta_value, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        _new_record_id("endpoint-meta"),
                        endpoint_id,
                        str(meta_key).strip(),
                        str(meta_value).strip(),
                        updated_at,
                    ),
                )

    def _list_endpoints_for_identity(
        self,
        connection: sqlite3.Connection,
        identity_id: str,
    ) -> list[EndpointRecord]:
        rows = connection.execute(
            """
            SELECT endpoint_id, identity_id, network, native_address, verified,
                   enabled, priority, trust_level, replication_class,
                   last_verified_at, capability_tags, updated_at
            FROM endpoints
            WHERE identity_id = ?
            ORDER BY priority DESC, endpoint_id
            """,
            (identity_id,),
        ).fetchall()
        return [
            EndpointRecord(
                endpoint_id=row[0],
                identity_id=row[1],
                network=row[2],
                native_address=row[3],
                verified=bool(row[4]),
                enabled=bool(row[5]),
                priority=int(row[6]),
                trust_level=row[7],
                replication_class=row[8],
                last_verified_at=row[9],
                capability_tags=row[10],
                updated_at=row[11],
            )
            for row in rows
        ]

    def _upsert_replication_state(
        self,
        connection: sqlite3.Connection,
        *,
        record_type: str,
        record_id: str,
        last_modified_at: str,
        replication_class: str,
        promotion_state: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO replication_state (
                record_type, record_id, origin_station_id, origin_version,
                last_modified_at, sync_priority, replication_class,
                conflict_flag, promotion_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_type, record_id) DO UPDATE SET
                origin_station_id = excluded.origin_station_id,
                origin_version = excluded.origin_version,
                last_modified_at = excluded.last_modified_at,
                sync_priority = excluded.sync_priority,
                replication_class = excluded.replication_class,
                conflict_flag = excluded.conflict_flag,
                promotion_state = excluded.promotion_state
            """,
            (
                record_type,
                record_id,
                self._origin_station_id,
                1,
                last_modified_at,
                100,
                replication_class,
                0,
                promotion_state,
            ),
        )

    def _record_audit_event(
        self,
        connection: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        subject_type: str,
        subject_id: str,
        summary: str,
        details: dict[str, object] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log (
                event_id, event_ts, actor, action, subject_type, subject_id,
                summary, source_station_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_record_id("audit"),
                _utc_now_iso(),
                str(actor).strip() or "system",
                str(action).strip() or "event",
                str(subject_type).strip() or "record",
                str(subject_id).strip(),
                str(summary).strip(),
                self._origin_station_id,
                json.dumps(details or {}, sort_keys=True),
            ),
        )

    def _entry_from_join_row(self, connection: sqlite3.Connection, row: sqlite3.Row | tuple[object, ...]) -> LocalUserEntry:
        user = UserRecord(
            user_id=row[0],
            display_name=row[1],
            status=row[2],
            home_region=row[3],
            is_local_to_station=bool(row[4]),
            retention_class=row[5],
            promotion_status=row[6],
            created_at=row[7],
            updated_at=row[8],
        )
        identity = IdentityRecord(
            identity_id=row[9],
            user_id=row[0],
            identity_type=row[10],
            display_name=row[11],
            messaging_mode=row[12],
            policy_id=row[13],
            status=row[14],
            created_at=row[15],
            updated_at=row[16],
        )
        endpoints = self._list_endpoints_for_identity(connection, identity.identity_id)
        profile = self._build_profile(connection, user, identity, endpoints)
        return LocalUserEntry(
            user=user,
            identity=identity,
            endpoints=tuple(endpoints),
            profile=profile,
            source_station=row[17],
            conflict_flag=bool(row[18]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10.0)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection