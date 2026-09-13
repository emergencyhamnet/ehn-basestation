from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class StationRecord:
    station_id: str
    display_name: str
    role: str
    db_path: str
    source: str
    updated_at: str
    owner_name: str = ""
    owner_org: str = ""
    deployment_status: str = "active"
    location_label: str = ""
    location_lat: float | None = None
    location_lon: float | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "station_id": self.station_id,
            "display_name": self.display_name,
            "role": self.role,
            "db_path": self.db_path,
            "source": self.source,
            "updated_at": self.updated_at,
            "owner_name": self.owner_name,
            "owner_org": self.owner_org,
            "deployment_status": self.deployment_status,
            "location_label": self.location_label,
            "location_lat": "" if self.location_lat is None else str(self.location_lat),
            "location_lon": "" if self.location_lon is None else str(self.location_lon),
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class StationCapabilityRecord:
    station_id: str
    supports_meshcore: bool
    supports_sms_gateway: bool
    supports_meshtastic: bool
    supports_email_fallback: bool
    supports_queue_claims: bool
    supports_encrypted_sync: bool
    last_verified_at: str | None
    updated_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "station_id": self.station_id,
            "supports_meshcore": str(self.supports_meshcore),
            "supports_sms_gateway": str(self.supports_sms_gateway),
            "supports_meshtastic": str(self.supports_meshtastic),
            "supports_email_fallback": str(self.supports_email_fallback),
            "supports_queue_claims": str(self.supports_queue_claims),
            "supports_encrypted_sync": str(self.supports_encrypted_sync),
            "last_verified_at": str(self.last_verified_at),
            "updated_at": self.updated_at,
        }


class StationStore:
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
                CREATE TABLE IF NOT EXISTS station_records (
                    station_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    db_path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner_name TEXT NOT NULL DEFAULT '',
                    owner_org TEXT NOT NULL DEFAULT '',
                    deployment_status TEXT NOT NULL DEFAULT 'active',
                    location_label TEXT NOT NULL DEFAULT '',
                    location_lat REAL,
                    location_lon REAL,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS station_contacts (
                    contact_id TEXT PRIMARY KEY,
                    station_id TEXT NOT NULL,
                    contact_role TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    phone TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    preferred_contact_method TEXT NOT NULL DEFAULT '',
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(station_id) REFERENCES station_records(station_id)
                );

                CREATE TABLE IF NOT EXISTS station_capabilities (
                    station_id TEXT PRIMARY KEY,
                    supports_meshcore INTEGER NOT NULL DEFAULT 0,
                    supports_sms_gateway INTEGER NOT NULL DEFAULT 0,
                    supports_meshtastic INTEGER NOT NULL DEFAULT 0,
                    supports_email_fallback INTEGER NOT NULL DEFAULT 0,
                    supports_queue_claims INTEGER NOT NULL DEFAULT 0,
                    supports_encrypted_sync INTEGER NOT NULL DEFAULT 0,
                    last_verified_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(station_id) REFERENCES station_records(station_id)
                );

                CREATE TABLE IF NOT EXISTS station_trust_material (
                    trust_material_id TEXT PRIMARY KEY,
                    station_id TEXT NOT NULL,
                    key_purpose TEXT NOT NULL,
                    key_identifier TEXT NOT NULL,
                    public_material TEXT NOT NULL DEFAULT '',
                    encrypted_private_material TEXT NOT NULL DEFAULT '',
                    material_status TEXT NOT NULL,
                    rotation_due_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(station_id) REFERENCES station_records(station_id)
                );

                CREATE TABLE IF NOT EXISTS station_audit_log (
                    event_id TEXT PRIMARY KEY,
                    event_ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    station_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY(station_id) REFERENCES station_records(station_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_stations_name
                ON station_records(display_name);

                CREATE INDEX IF NOT EXISTS idx_stations_role_status
                ON station_records(role, deployment_status);

                CREATE INDEX IF NOT EXISTS idx_station_contacts_station
                ON station_contacts(station_id);

                CREATE INDEX IF NOT EXISTS idx_station_contacts_primary
                ON station_contacts(station_id, is_primary);

                CREATE INDEX IF NOT EXISTS idx_station_trust_station
                ON station_trust_material(station_id);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_station_trust_key
                ON station_trust_material(station_id, key_purpose, key_identifier);
                """
            )
            self._ensure_schema_columns(connection)
            connection.commit()

    def _ensure_schema_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(station_records)").fetchall()
        }
        for column_name, column_sql in (
            ("owner_name", "TEXT NOT NULL DEFAULT ''"),
            ("owner_org", "TEXT NOT NULL DEFAULT ''"),
            ("deployment_status", "TEXT NOT NULL DEFAULT 'active'"),
            ("location_label", "TEXT NOT NULL DEFAULT ''"),
            ("location_lat", "REAL"),
            ("location_lon", "REAL"),
            ("notes", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column_name not in columns:
                connection.execute(
                    f"ALTER TABLE station_records ADD COLUMN {column_name} {column_sql}"
                )

    def upsert_station_record(
        self,
        *,
        station_id: str,
        display_name: str,
        role: str,
        db_path: str,
        source: str,
        updated_at: str | None = None,
        owner_name: str = "",
        owner_org: str = "",
        deployment_status: str = "active",
        location_label: str = "",
        location_lat: float | None = None,
        location_lon: float | None = None,
        notes: str = "",
    ) -> StationRecord:
        record = StationRecord(
            station_id=str(station_id).strip(),
            display_name=str(display_name).strip(),
            role=str(role).strip(),
            db_path=str(db_path).strip(),
            source=str(source).strip(),
            updated_at=str(updated_at or _utc_now_iso()).strip(),
            owner_name=str(owner_name).strip(),
            owner_org=str(owner_org).strip(),
            deployment_status=str(deployment_status).strip() or "active",
            location_label=str(location_label).strip(),
            location_lat=location_lat,
            location_lon=location_lon,
            notes=str(notes).strip(),
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO station_records (
                    station_id, display_name, role, db_path, source, updated_at,
                    owner_name, owner_org, deployment_status, location_label, location_lat, location_lon, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    role = excluded.role,
                    db_path = excluded.db_path,
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    owner_name = excluded.owner_name,
                    owner_org = excluded.owner_org,
                    deployment_status = excluded.deployment_status,
                    location_label = excluded.location_label,
                    location_lat = excluded.location_lat,
                    location_lon = excluded.location_lon,
                    notes = excluded.notes
                """,
                (
                    record.station_id,
                    record.display_name,
                    record.role,
                    record.db_path,
                    record.source,
                    record.updated_at,
                    record.owner_name,
                    record.owner_org,
                    record.deployment_status,
                    record.location_label,
                    record.location_lat,
                    record.location_lon,
                    record.notes,
                ),
            )
            connection.commit()
        return record

    def upsert_station_capabilities(
        self,
        *,
        station_id: str,
        supports_meshcore: bool = False,
        supports_sms_gateway: bool = False,
        supports_meshtastic: bool = False,
        supports_email_fallback: bool = False,
        supports_queue_claims: bool = False,
        supports_encrypted_sync: bool = False,
        last_verified_at: str | None = None,
        updated_at: str | None = None,
    ) -> StationCapabilityRecord:
        record = StationCapabilityRecord(
            station_id=str(station_id).strip(),
            supports_meshcore=bool(supports_meshcore),
            supports_sms_gateway=bool(supports_sms_gateway),
            supports_meshtastic=bool(supports_meshtastic),
            supports_email_fallback=bool(supports_email_fallback),
            supports_queue_claims=bool(supports_queue_claims),
            supports_encrypted_sync=bool(supports_encrypted_sync),
            last_verified_at=str(last_verified_at).strip() if last_verified_at else None,
            updated_at=str(updated_at or _utc_now_iso()).strip(),
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO station_capabilities (
                    station_id, supports_meshcore, supports_sms_gateway, supports_meshtastic,
                    supports_email_fallback, supports_queue_claims, supports_encrypted_sync,
                    last_verified_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(station_id) DO UPDATE SET
                    supports_meshcore = excluded.supports_meshcore,
                    supports_sms_gateway = excluded.supports_sms_gateway,
                    supports_meshtastic = excluded.supports_meshtastic,
                    supports_email_fallback = excluded.supports_email_fallback,
                    supports_queue_claims = excluded.supports_queue_claims,
                    supports_encrypted_sync = excluded.supports_encrypted_sync,
                    last_verified_at = excluded.last_verified_at,
                    updated_at = excluded.updated_at
                """,
                (
                    record.station_id,
                    1 if record.supports_meshcore else 0,
                    1 if record.supports_sms_gateway else 0,
                    1 if record.supports_meshtastic else 0,
                    1 if record.supports_email_fallback else 0,
                    1 if record.supports_queue_claims else 0,
                    1 if record.supports_encrypted_sync else 0,
                    record.last_verified_at,
                    record.updated_at,
                ),
            )
            connection.commit()
        return record

    def get_station_capabilities(self, station_id: str) -> StationCapabilityRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT station_id, supports_meshcore, supports_sms_gateway, supports_meshtastic,
                       supports_email_fallback, supports_queue_claims, supports_encrypted_sync,
                       last_verified_at, updated_at
                FROM station_capabilities
                WHERE station_id = ?
                """,
                (str(station_id).strip(),),
            ).fetchone()
        if row is None:
            return None
        return StationCapabilityRecord(
            station_id=row[0],
            supports_meshcore=bool(row[1]),
            supports_sms_gateway=bool(row[2]),
            supports_meshtastic=bool(row[3]),
            supports_email_fallback=bool(row[4]),
            supports_queue_claims=bool(row[5]),
            supports_encrypted_sync=bool(row[6]),
            last_verified_at=row[7],
            updated_at=row[8],
        )

    def get_station_record(self, station_id: str) -> StationRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                  SELECT station_id, display_name, role, db_path, source, updated_at,
                      owner_name, owner_org, deployment_status, location_label,
                      location_lat, location_lon, notes
                FROM station_records
                WHERE station_id = ?
                """,
                (str(station_id).strip(),),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_station_records(self) -> list[StationRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT station_id, display_name, role, db_path, source, updated_at,
                       owner_name, owner_org, deployment_status, location_label,
                       location_lat, location_lon, notes
                FROM station_records
                ORDER BY station_id
                """
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10.0)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _row_to_record(self, row: sqlite3.Row | tuple[object, ...]) -> StationRecord:
        (
            station_id,
            display_name,
            role,
            db_path,
            source,
            updated_at,
            owner_name,
            owner_org,
            deployment_status,
            location_label,
            location_lat,
            location_lon,
            notes,
        ) = row
        return StationRecord(
            station_id=station_id,
            display_name=display_name,
            role=role,
            db_path=db_path,
            source=source,
            updated_at=updated_at,
            owner_name=owner_name,
            owner_org=owner_org,
            deployment_status=deployment_status,
            location_label=location_label,
            location_lat=location_lat,
            location_lon=location_lon,
            notes=notes,
        )