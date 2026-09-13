from __future__ import annotations

from dataclasses import dataclass
import socket

from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.directory.station_store import StationRecord, StationStore
from ehn_basestation.gateways.meshcore.gateway_link import (
    GatewayLinkEnvelope,
    decode_gateway_link_frame,
    encode_gateway_link_frame,
)


PROTOCOL_VERSION = "ehn-gateway-link/1"


def build_station_record(settings: BasestationSettings, *, source: str) -> StationRecord:
    return StationRecord(
        station_id=settings.station_id,
        display_name=settings.station_name.replace("-", " ").title(),
        role=settings.station_role,
        db_path=str(settings.active_directory_db_path),
        source=source,
        updated_at="",
    )


def initialize_station_db(settings: BasestationSettings) -> StationRecord:
    store = StationStore(settings.registry_db_path)
    store.initialize()
    DirectoryStore(
        settings.active_directory_db_path,
        origin_station_id=settings.station_id,
    ).initialize()
    station_record = build_station_record(settings, source="self")
    return store.upsert_station_record(
        station_id=station_record.station_id,
        display_name=station_record.display_name,
        role=station_record.role,
        db_path=station_record.db_path,
        source="self",
    )


def list_station_records(settings: BasestationSettings) -> list[dict[str, str]]:
    store = StationStore(settings.registry_db_path)
    store.initialize()
    return [record.as_dict() for record in store.list_station_records()]


@dataclass(frozen=True, slots=True)
class SyncResult:
    local_station: dict[str, str]
    remote_station: dict[str, str]
    remote_endpoint: str


class PeerStationLinkServer:
    def __init__(self, settings: BasestationSettings, *, host: str, port: int) -> None:
        self._settings = settings
        self._host = str(host).strip() or "127.0.0.1"
        self._port = int(port)
        self._store = StationStore(settings.registry_db_path)
        self._store.initialize()
        initialize_station_db(settings)

    def serve_forever(self) -> None:
        with socket.create_server((self._host, self._port), reuse_port=False) as server:
            while True:
                connection, _ = server.accept()
                with connection:
                    self._handle_connection(connection)

    def serve_once(self) -> None:
        with socket.create_server((self._host, self._port), reuse_port=False) as server:
            connection, _ = server.accept()
            with connection:
                self._handle_connection(connection)

    def _handle_connection(self, connection: socket.socket) -> None:
        envelope = _recv_envelope(connection)
        if envelope.type != "request" or envelope.method != "hello":
            response = GatewayLinkEnvelope(
                version=PROTOCOL_VERSION,
                type="response",
                id=envelope.id,
                ok=False,
                error={"message": f"Unsupported method {envelope.method or envelope.type}."},
            )
            connection.sendall(encode_gateway_link_frame(response))
            return

        station_payload = envelope.params.get("station") if isinstance(envelope.params, dict) else None
        if not isinstance(station_payload, dict):
            response = GatewayLinkEnvelope(
                version=PROTOCOL_VERSION,
                type="response",
                id=envelope.id,
                ok=False,
                error={"message": "hello request requires station payload."},
            )
            connection.sendall(encode_gateway_link_frame(response))
            return

        remote_record = self._store.upsert_station_record(
            station_id=str(station_payload.get("station_id") or "").strip(),
            display_name=str(station_payload.get("display_name") or "").strip(),
            role=str(station_payload.get("role") or "peer").strip(),
            db_path=str(station_payload.get("db_path") or "").strip(),
            source="peer-link",
            updated_at=str(station_payload.get("updated_at") or "").strip() or None,
        )
        self_record = self._store.get_station_record(self._settings.station_id)
        response = GatewayLinkEnvelope(
            version=PROTOCOL_VERSION,
            type="response",
            id=envelope.id,
            ok=True,
            result={
                "accepted_station": remote_record.as_dict(),
                "server_station": self_record.as_dict() if self_record is not None else {},
            },
        )
        connection.sendall(encode_gateway_link_frame(response))


def sync_station_with_peer(settings: BasestationSettings, *, host: str, port: int, timeout_seconds: float = 5.0) -> SyncResult:
    store = StationStore(settings.registry_db_path)
    store.initialize()
    local_record = initialize_station_db(settings)

    request = GatewayLinkEnvelope(
        version=PROTOCOL_VERSION,
        type="request",
        id=f"hello-{settings.station_id}",
        method="hello",
        params={"station": local_record.as_dict()},
    )

    with socket.create_connection((str(host).strip() or "127.0.0.1", int(port)), timeout=timeout_seconds) as connection:
        connection.sendall(encode_gateway_link_frame(request))
        response = _recv_envelope(connection)

    if response.type != "response" or not response.ok:
        message = (response.error or {}).get("message") if isinstance(response.error, dict) else None
        raise RuntimeError(str(message or "Peer station sync failed."))

    server_station = response.result.get("server_station") if isinstance(response.result, dict) else None
    if not isinstance(server_station, dict):
        raise RuntimeError("Peer station sync did not return a server station record.")
    persisted_remote = store.upsert_station_record(
        station_id=str(server_station.get("station_id") or "").strip(),
        display_name=str(server_station.get("display_name") or "").strip(),
        role=str(server_station.get("role") or "peer").strip(),
        db_path=str(server_station.get("db_path") or "").strip(),
        source="peer-link",
        updated_at=str(server_station.get("updated_at") or "").strip() or None,
    )
    return SyncResult(
        local_station=local_record.as_dict(),
        remote_station=persisted_remote.as_dict(),
        remote_endpoint=f"{host}:{port}",
    )


def _recv_envelope(connection: socket.socket) -> GatewayLinkEnvelope:
    header = _recv_exact(connection, 4)
    payload_length = int.from_bytes(header, byteorder="big", signed=False)
    payload = _recv_exact(connection, payload_length)
    return decode_gateway_link_frame(header + payload)


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("Peer station link closed before the frame completed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)