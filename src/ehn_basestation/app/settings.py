from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path).expanduser().resolve()
    loaded: dict[str, str] = {}
    if not env_path.exists():
        raise FileNotFoundError(f"Environment file not found: {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        os.environ[normalized_key] = normalized_value
        loaded[normalized_key] = normalized_value
    return loaded


def _env_flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized not in {"0", "false", "no", "off", ""}


def _env_text(env: Mapping[str, str], name: str, default: str) -> str:
    if name not in env:
        return default
    return str(env.get(name) or "").strip()


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return int(text)


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    value = env.get(name)
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return float(text)


@dataclass(frozen=True, slots=True)
class BasestationSettings:
    project_root: Path
    instance_root: Path
    active_directory_db_path: Path
    authority_directory_db_path: Path
    peer_directory_db_path: Path
    registry_db_path: Path
    delivery_db_path: Path
    replication_root: Path
    log_root: Path
    station_id: str
    station_name: str
    station_role: str
    station_link_bind_host: str
    station_link_port: int
    peer_station_host: str
    peer_station_port: int
    meshcore_gateway_id: str
    meshcore_display_name: str
    meshcore_link_carrier: str
    meshcore_link_endpoint: str
    meshcore_base_url: str
    meshcore_timeout_seconds: float
    meshcore_contract_version: str
    meshcore_auth_token: str | None
    meshcore_enabled: bool
    meshtastic_gateway_id: str
    meshtastic_display_name: str
    meshtastic_serial_port: str
    meshtastic_group_channel_index: int
    meshtastic_enabled: bool
    sms_gateway_id: str
    sms_display_name: str
    sms_android_auth_token: str | None
    sms_enabled: bool
    maintenance_interval_seconds: int

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> BasestationSettings:
        resolved_env = env or os.environ
        resolved_project_root = (project_root or resolve_project_root()).resolve()
        instance_root = Path(
            resolved_env.get("EHN_BASESTATION_INSTANCE_ROOT")
            or resolved_project_root / "var"
        ).resolve()
        local_station_root = instance_root / "stations" / "local"
        peer_station_root = instance_root / "stations" / "peer"
        authority_root = instance_root / "authority"
        active_directory_db_path = Path(
            resolved_env.get("BASESTATION_DIRECTORY_DB_PATH")
            or local_station_root / "directory" / "ehn-directory.db"
        ).resolve()
        authority_directory_db_path = Path(
            resolved_env.get("EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH")
            or authority_root / "directory" / "ehn-authority.db"
        ).resolve()
        peer_directory_db_path = Path(
            resolved_env.get("EHN_BASESTATION_PEER_DIRECTORY_DB_PATH")
            or peer_station_root / "directory" / "ehn-directory.db"
        ).resolve()
        station_data_root = active_directory_db_path.parent.parent
        registry_db_path = Path(
            resolved_env.get("EHN_BASESTATION_REGISTRY_DB_PATH")
            or station_data_root / "registry" / "ehn-stations.db"
        ).resolve()
        delivery_db_path = Path(
            resolved_env.get("EHN_BASESTATION_DELIVERY_DB_PATH")
            or station_data_root / "delivery" / "ehn-delivery.db"
        ).resolve()
        replication_root = Path(
            resolved_env.get("EHN_BASESTATION_REPLICATION_ROOT")
            or instance_root / "replication" / "bundles"
        ).resolve()
        log_root = Path(
            resolved_env.get("EHN_BASESTATION_LOG_ROOT")
            or local_station_root / "logs"
        ).resolve()
        station_name = _env_text(
            resolved_env,
            "EHN_BASESTATION_STATION_NAME",
            "local-station",
        ) or "local-station"
        station_id = _env_text(
            resolved_env,
            "EHN_BASESTATION_STATION_ID",
            station_name,
        ) or station_name
        station_role = _env_text(
            resolved_env,
            "EHN_BASESTATION_STATION_ROLE",
            "field",
        ).lower() or "field"
        station_link_bind_host = _env_text(
            resolved_env,
            "EHN_BASESTATION_LINK_BIND_HOST",
            "127.0.0.1",
        ) or "127.0.0.1"
        station_link_port = _env_int(
            resolved_env,
            "EHN_BASESTATION_LINK_PORT",
            7462,
        )
        peer_station_host = _env_text(
            resolved_env,
            "EHN_BASESTATION_PEER_HOST",
            "127.0.0.1",
        ) or "127.0.0.1"
        peer_station_port = _env_int(
            resolved_env,
            "EHN_BASESTATION_PEER_PORT",
            station_link_port,
        )
        meshcore_gateway_id = _env_text(
            resolved_env,
            "BASESTATION_MESHCORE_GATEWAY_ID",
            "meshcore-gateway-1",
        ) or "meshcore-gateway-1"
        meshcore_display_name = _env_text(
            resolved_env,
            "BASESTATION_MESHCORE_DISPLAY_NAME",
            "MeshCore Gateway",
        ) or "MeshCore Gateway"
        meshcore_link_carrier = _env_text(
            resolved_env,
            "BASESTATION_MESHCORE_LINK_CARRIER",
            "http",
        ).lower() or "http"
        meshcore_link_endpoint = _env_text(
            resolved_env,
            "BASESTATION_MESHCORE_LINK_ENDPOINT",
            "",
        )
        meshcore_base_url = _env_text(
            resolved_env,
            "BASESTATION_MESHCORE_BASE_URL",
            "http://127.0.0.1:8088",
        )
        meshcore_timeout_seconds = _env_float(
            resolved_env,
            "BASESTATION_MESHCORE_TIMEOUT_SECONDS",
            5.0,
        )
        meshcore_contract_version = _env_text(
            resolved_env,
            "BASESTATION_MESHCORE_CONTRACT_VERSION",
            "1.0",
        ) or "1.0"
        meshcore_auth_token = str(
            resolved_env.get("BASESTATION_MESHCORE_AUTH_TOKEN") or ""
        ).strip() or None
        meshcore_enabled = _env_flag(resolved_env, "BASESTATION_MESHCORE_ENABLED", True)
        meshtastic_gateway_id = _env_text(
            resolved_env,
            "BASESTATION_MESHTASTIC_GATEWAY_ID",
            "meshtastic-gateway-1",
        ) or "meshtastic-gateway-1"
        meshtastic_display_name = _env_text(
            resolved_env,
            "BASESTATION_MESHTASTIC_DISPLAY_NAME",
            "Meshtastic Gateway",
        ) or "Meshtastic Gateway"
        meshtastic_serial_port = _env_text(
            resolved_env,
            "BASESTATION_MESHTASTIC_SERIAL_PORT",
            "COM8",
        )
        meshtastic_group_channel_index = max(
            0,
            _env_int(resolved_env, "BASESTATION_MESHTASTIC_GROUP_CHANNEL_INDEX", 1),
        )
        meshtastic_enabled = _env_flag(resolved_env, "BASESTATION_MESHTASTIC_ENABLED", False)
        sms_gateway_id = _env_text(
            resolved_env,
            "BASESTATION_SMS_GATEWAY_ID",
            "android-sms-gateway-1",
        ) or "android-sms-gateway-1"
        sms_display_name = _env_text(
            resolved_env,
            "BASESTATION_SMS_DISPLAY_NAME",
            "Android SMS Gateway",
        ) or "Android SMS Gateway"
        sms_android_auth_token = str(
            resolved_env.get("BASESTATION_SMS_ANDROID_AUTH_TOKEN") or ""
        ).strip() or None
        sms_enabled = _env_flag(resolved_env, "BASESTATION_SMS_ENABLED", True)
        # 0 disables the in-process delivery maintenance scheduler.
        maintenance_interval_seconds = _env_int(resolved_env, "EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS", 60)

        return cls(
            project_root=resolved_project_root,
            instance_root=instance_root,
            active_directory_db_path=active_directory_db_path,
            authority_directory_db_path=authority_directory_db_path,
            peer_directory_db_path=peer_directory_db_path,
            registry_db_path=registry_db_path,
            delivery_db_path=delivery_db_path,
            replication_root=replication_root,
            log_root=log_root,
            station_id=station_id,
            station_name=station_name,
            station_role=station_role,
            station_link_bind_host=station_link_bind_host,
            station_link_port=station_link_port,
            peer_station_host=peer_station_host,
            peer_station_port=peer_station_port,
            meshcore_gateway_id=meshcore_gateway_id,
            meshcore_display_name=meshcore_display_name,
            meshcore_link_carrier=meshcore_link_carrier,
            meshcore_link_endpoint=meshcore_link_endpoint,
            meshcore_base_url=meshcore_base_url,
            meshcore_timeout_seconds=meshcore_timeout_seconds,
            meshcore_contract_version=meshcore_contract_version,
            meshcore_auth_token=meshcore_auth_token,
            meshcore_enabled=meshcore_enabled,
            meshtastic_gateway_id=meshtastic_gateway_id,
            meshtastic_display_name=meshtastic_display_name,
            meshtastic_serial_port=meshtastic_serial_port,
            meshtastic_group_channel_index=meshtastic_group_channel_index,
            meshtastic_enabled=meshtastic_enabled,
            sms_gateway_id=sms_gateway_id,
            sms_display_name=sms_display_name,
            sms_android_auth_token=sms_android_auth_token,
            sms_enabled=sms_enabled,
            maintenance_interval_seconds=maintenance_interval_seconds,
        )

    def with_meshcore_base_url(self, meshcore_base_url: str) -> BasestationSettings:
        return BasestationSettings(
            project_root=self.project_root,
            instance_root=self.instance_root,
            active_directory_db_path=self.active_directory_db_path,
            authority_directory_db_path=self.authority_directory_db_path,
            peer_directory_db_path=self.peer_directory_db_path,
            registry_db_path=self.registry_db_path,
            delivery_db_path=self.delivery_db_path,
            replication_root=self.replication_root,
            log_root=self.log_root,
            station_id=self.station_id,
            station_name=self.station_name,
            station_role=self.station_role,
            station_link_bind_host=self.station_link_bind_host,
            station_link_port=self.station_link_port,
            peer_station_host=self.peer_station_host,
            peer_station_port=self.peer_station_port,
            meshcore_gateway_id=self.meshcore_gateway_id,
            meshcore_display_name=self.meshcore_display_name,
            meshcore_link_carrier=self.meshcore_link_carrier,
            meshcore_link_endpoint=self.meshcore_link_endpoint,
            meshcore_base_url=str(meshcore_base_url).strip(),
            meshcore_timeout_seconds=self.meshcore_timeout_seconds,
            meshcore_contract_version=self.meshcore_contract_version,
            meshcore_auth_token=self.meshcore_auth_token,
            meshcore_enabled=self.meshcore_enabled,
            meshtastic_gateway_id=self.meshtastic_gateway_id,
            meshtastic_display_name=self.meshtastic_display_name,
            meshtastic_serial_port=self.meshtastic_serial_port,
            meshtastic_group_channel_index=self.meshtastic_group_channel_index,
            meshtastic_enabled=self.meshtastic_enabled,
            sms_gateway_id=self.sms_gateway_id,
            sms_display_name=self.sms_display_name,
            sms_android_auth_token=self.sms_android_auth_token,
            sms_enabled=self.sms_enabled,
            maintenance_interval_seconds=self.maintenance_interval_seconds,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "project_root": str(self.project_root),
            "instance_root": str(self.instance_root),
            "active_directory_db_path": str(self.active_directory_db_path),
            "authority_directory_db_path": str(self.authority_directory_db_path),
            "peer_directory_db_path": str(self.peer_directory_db_path),
            "registry_db_path": str(self.registry_db_path),
            "delivery_db_path": str(self.delivery_db_path),
            "replication_root": str(self.replication_root),
            "log_root": str(self.log_root),
            "station_id": self.station_id,
            "station_name": self.station_name,
            "station_role": self.station_role,
            "station_link_bind_host": self.station_link_bind_host,
            "station_link_port": str(self.station_link_port),
            "peer_station_host": self.peer_station_host,
            "peer_station_port": str(self.peer_station_port),
            "meshcore_gateway_id": self.meshcore_gateway_id,
            "meshcore_display_name": self.meshcore_display_name,
            "meshcore_link_carrier": self.meshcore_link_carrier,
            "meshcore_link_endpoint": self.meshcore_link_endpoint,
            "meshcore_base_url": self.meshcore_base_url,
            "meshcore_timeout_seconds": str(self.meshcore_timeout_seconds),
            "meshcore_contract_version": self.meshcore_contract_version,
            "meshcore_auth_token": str(self.meshcore_auth_token),
            "meshcore_enabled": str(self.meshcore_enabled),
            "meshtastic_gateway_id": self.meshtastic_gateway_id,
            "meshtastic_display_name": self.meshtastic_display_name,
            "meshtastic_serial_port": self.meshtastic_serial_port,
            "meshtastic_group_channel_index": str(self.meshtastic_group_channel_index),
            "meshtastic_enabled": str(self.meshtastic_enabled),
            "sms_gateway_id": self.sms_gateway_id,
            "sms_display_name": self.sms_display_name,
            "sms_android_auth_token": str(self.sms_android_auth_token),
            "sms_enabled": str(self.sms_enabled),
            "maintenance_interval_seconds": str(self.maintenance_interval_seconds),
        }