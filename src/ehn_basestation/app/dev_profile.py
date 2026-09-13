from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ehn_basestation.app.settings import BasestationSettings


@dataclass(frozen=True, slots=True)
class DevelopmentLayout:
    created_directories: tuple[Path, ...]
    written_files: tuple[Path, ...]


def initialize_windows_dev_layout(
    *,
    settings: BasestationSettings,
    force: bool = False,
) -> DevelopmentLayout:
    directories = (
        settings.instance_root,
        settings.instance_root / "stations",
        settings.instance_root / "stations" / "local",
        settings.active_directory_db_path.parent,
        settings.authority_directory_db_path.parent,
        settings.log_root,
        settings.instance_root / "stations" / "local" / "runtime",
        settings.instance_root / "stations" / "peer",
        settings.peer_directory_db_path.parent,
        settings.instance_root / "stations" / "peer" / "logs",
        settings.instance_root / "stations" / "peer" / "runtime",
        settings.replication_root,
        settings.project_root / "dev",
        settings.project_root / "dev" / "windows",
        settings.project_root / "dev" / "windows" / "env",
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    local_env_path = settings.project_root / "dev" / "windows" / "env" / "local-station.env"
    peer_env_path = settings.project_root / "dev" / "windows" / "env" / "peer-station.env"
    authority_env_path = settings.project_root / "dev" / "windows" / "env" / "authority-station.env"

    _write_file(
        local_env_path,
        _render_env_file(
            station_name="local-station",
            station_role="field",
            active_directory_db_path=settings.active_directory_db_path,
            authority_directory_db_path=settings.authority_directory_db_path,
            peer_directory_db_path=settings.peer_directory_db_path,
            replication_root=settings.replication_root,
            log_root=settings.log_root,
            station_link_bind_host="127.0.0.1",
            station_link_port=7462,
            peer_station_host="127.0.0.1",
            peer_station_port=7462,
            meshcore_link_carrier=settings.meshcore_link_carrier,
            meshcore_link_endpoint=settings.meshcore_link_endpoint,
            meshcore_base_url=settings.meshcore_base_url,
            meshcore_enabled=settings.meshcore_enabled,
        ),
        force=force,
    )
    _write_file(
        peer_env_path,
        _render_env_file(
            station_name="peer-station",
            station_role="field",
            active_directory_db_path=settings.peer_directory_db_path,
            authority_directory_db_path=settings.authority_directory_db_path,
            peer_directory_db_path=settings.active_directory_db_path,
            replication_root=settings.replication_root,
            log_root=settings.instance_root / "stations" / "peer" / "logs",
            station_link_bind_host="127.0.0.1",
            station_link_port=7462,
            peer_station_host="127.0.0.1",
            peer_station_port=7462,
            meshcore_link_carrier=settings.meshcore_link_carrier,
            meshcore_link_endpoint=settings.meshcore_link_endpoint,
            meshcore_base_url=settings.meshcore_base_url,
            meshcore_enabled=False,
        ),
        force=force,
    )
    _write_file(
        authority_env_path,
        _render_env_file(
            station_name="authority-station",
            station_role="authority",
            active_directory_db_path=settings.authority_directory_db_path,
            authority_directory_db_path=settings.authority_directory_db_path,
            peer_directory_db_path=settings.active_directory_db_path,
            replication_root=settings.replication_root,
            log_root=settings.instance_root / "authority" / "logs",
            station_link_bind_host="127.0.0.1",
            station_link_port=7464,
            peer_station_host="127.0.0.1",
            peer_station_port=7462,
            meshcore_link_carrier=settings.meshcore_link_carrier,
            meshcore_link_endpoint=settings.meshcore_link_endpoint,
            meshcore_base_url=settings.meshcore_base_url,
            meshcore_enabled=False,
        ),
        force=force,
    )

    return DevelopmentLayout(
        created_directories=directories,
        written_files=(local_env_path, peer_env_path, authority_env_path),
    )


def _render_env_file(
    *,
    station_name: str,
    station_role: str,
    active_directory_db_path: Path,
    authority_directory_db_path: Path,
    peer_directory_db_path: Path,
    replication_root: Path,
    log_root: Path,
    station_link_bind_host: str,
    station_link_port: int,
    peer_station_host: str,
    peer_station_port: int,
    meshcore_link_carrier: str,
    meshcore_link_endpoint: str,
    meshcore_base_url: str,
    meshcore_enabled: bool,
) -> str:
    lines = (
        f"EHN_BASESTATION_STATION_NAME={station_name}",
        f"EHN_BASESTATION_STATION_ROLE={station_role}",
        f"BASESTATION_DIRECTORY_DB_PATH={active_directory_db_path}",
        f"EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH={authority_directory_db_path}",
        f"EHN_BASESTATION_PEER_DIRECTORY_DB_PATH={peer_directory_db_path}",
        f"EHN_BASESTATION_REPLICATION_ROOT={replication_root}",
        f"EHN_BASESTATION_LOG_ROOT={log_root}",
        f"EHN_BASESTATION_LINK_BIND_HOST={station_link_bind_host}",
        f"EHN_BASESTATION_LINK_PORT={station_link_port}",
        f"EHN_BASESTATION_PEER_HOST={peer_station_host}",
        f"EHN_BASESTATION_PEER_PORT={peer_station_port}",
        f"BASESTATION_MESHCORE_ENABLED={1 if meshcore_enabled else 0}",
        "BASESTATION_MESHCORE_GATEWAY_ID=meshcore-gateway-1",
        "BASESTATION_MESHCORE_DISPLAY_NAME=MeshCore Gateway",
        "BASESTATION_MESHCORE_TRANSPORT=meshcore-runtime",
        f"BASESTATION_MESHCORE_LINK_CARRIER={meshcore_link_carrier}",
        f"BASESTATION_MESHCORE_LINK_ENDPOINT={meshcore_link_endpoint}",
        f"BASESTATION_MESHCORE_BASE_URL={meshcore_base_url}",
        "BASESTATION_MESHCORE_CONTRACT_VERSION=1.0",
        "",
    )
    return "\n".join(lines)


def _write_file(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.write_text(content, encoding="utf-8")