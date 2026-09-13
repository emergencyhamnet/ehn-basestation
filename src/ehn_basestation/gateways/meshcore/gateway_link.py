from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Protocol
from urllib import error, parse, request
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class MeshCoreRuntimeSettings:
    gateway_id: str
    display_name: str
    base_url: str
    contract_version: str
    enabled: bool = True
    auth_token: str | None = None
    timeout_seconds: float = 5.0


class MeshCoreGatewayLink(Protocol):
    @property
    def gateway_id(self) -> str: ...

    def get_snapshot(self) -> dict[str, Any]: ...

    def list_channels(self) -> list[dict[str, Any]]: ...

    def list_observed_nodes(self) -> list[dict[str, Any]]: ...

    def list_gateway_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]: ...

    def list_gateway_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]: ...

    def get_device_details(self) -> dict[str, Any]: ...

    def set_tx_power(self, *, tx_power_dbm: int) -> tuple[bool, str, dict[str, Any] | None]: ...

    def send_directed_private_message(
        self,
        *,
        recipient_public_key: str,
        text: str,
        sender_identity: dict[str, Any] | None = None,
        destination_identity: dict[str, Any] | None = None,
        delivery_id: str | None = None,
        reply_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]: ...

    def send_channel_message(
        self,
        *,
        channel_index: int,
        text: str,
        channel_name: str | None = None,
        delivery_id: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]: ...


@dataclass(frozen=True, slots=True)
class GatewayLinkEnvelope:
    version: str
    type: str
    id: str | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    ok: bool | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    event: str | None = None
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "version": self.version,
            "type": self.type,
        }
        optional_fields = {
            "id": self.id,
            "method": self.method,
            "params": self.params,
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
            "event": self.event,
            "payload": self.payload,
        }
        for key, value in optional_fields.items():
            if value is not None:
                data[key] = value
        return data

    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GatewayLinkEnvelope:
        return cls(
            version=str(payload.get("version") or "").strip(),
            type=str(payload.get("type") or "").strip(),
            id=str(payload.get("id") or "").strip() or None,
            method=str(payload.get("method") or "").strip() or None,
            params=payload.get("params") if isinstance(payload.get("params"), dict) else None,
            ok=payload.get("ok") if isinstance(payload.get("ok"), bool) else None,
            result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
            error=payload.get("error") if isinstance(payload.get("error"), dict) else None,
            event=str(payload.get("event") or "").strip() or None,
            payload=payload.get("payload") if isinstance(payload.get("payload"), dict) else None,
        )


def encode_gateway_link_frame(envelope: GatewayLinkEnvelope) -> bytes:
    payload = envelope.to_json_bytes()
    return len(payload).to_bytes(4, byteorder="big", signed=False) + payload


def decode_gateway_link_frame(frame: bytes) -> GatewayLinkEnvelope:
    if len(frame) < 4:
        raise ValueError("Gateway-link frame is too short.")
    declared_length = int.from_bytes(frame[:4], byteorder="big", signed=False)
    payload = frame[4:]
    if len(payload) != declared_length:
        raise ValueError("Gateway-link frame length does not match payload size.")
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Gateway-link frame payload must decode to a JSON object.")
    return GatewayLinkEnvelope.from_dict(data)


class StreamMeshCoreGatewayLink:
    protocol_version = "ehn-gateway-link/1"

    def __init__(self, settings: MeshCoreRuntimeSettings, *, carrier: str, endpoint: str) -> None:
        self._settings = settings
        self._carrier = str(carrier).strip() or "stream"
        self._endpoint = str(endpoint).strip()

    @property
    def gateway_id(self) -> str:
        return self._settings.gateway_id

    def get_snapshot(self) -> dict[str, Any]:
        try:
            result = self._request("get_snapshot")
        except Exception as exc:
            return {
                "connected": False,
                "status": "unreachable",
                "gateway_id": self.gateway_id,
                "display_name": self._settings.display_name,
                "contract_version": self._settings.contract_version,
                "identity": {},
                "runtime_status": {
                    "transport": "meshcore-gateway-link",
                    "link_carrier": self._carrier,
                    "link_endpoint": self._endpoint,
                    "link_version": self.protocol_version,
                },
                "last_error": str(exc),
            }
        snapshot = dict(result.get("snapshot") if isinstance(result.get("snapshot"), dict) else result)
        runtime_status = snapshot.get("runtime_status") if isinstance(snapshot.get("runtime_status"), dict) else {}
        merged_runtime_status = {
            **runtime_status,
            "transport": "meshcore-gateway-link",
            "link_carrier": self._carrier,
            "link_endpoint": self._endpoint,
            "link_version": self.protocol_version,
        }
        snapshot["runtime_status"] = merged_runtime_status
        snapshot.setdefault("gateway_id", self.gateway_id)
        snapshot.setdefault("display_name", self._settings.display_name)
        snapshot.setdefault("contract_version", self._settings.contract_version)
        snapshot.setdefault("connected", True)
        snapshot.setdefault("status", "connected")
        snapshot.setdefault("identity", {})
        return snapshot

    def list_channels(self) -> list[dict[str, Any]]:
        try:
            result = self._request("list_channels")
        except Exception:
            return []
        channels = result.get("channels") if isinstance(result.get("channels"), list) else []
        return [dict(channel) for channel in channels if isinstance(channel, dict)]

    def list_observed_nodes(self) -> list[dict[str, Any]]:
        try:
            result = self._request("list_observed_nodes")
        except Exception:
            return []
        observed_nodes = result.get("observed_nodes") if isinstance(result.get("observed_nodes"), list) else []
        return [dict(node) for node in observed_nodes if isinstance(node, dict)]

    def list_gateway_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if cursor:
            params["cursor"] = str(cursor)
        try:
            result = self._request("list_gateway_inbound_events", params=params)
        except Exception:
            return []
        events = result.get("events") if isinstance(result.get("events"), list) else []
        return [dict(event) for event in events if isinstance(event, dict)]

    def list_gateway_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if state:
            params["state"] = str(state)
        try:
            result = self._request("list_gateway_outbound_status", params=params)
        except Exception:
            return []
        requests_payload = result.get("requests") if isinstance(result.get("requests"), list) else []
        return [dict(item) for item in requests_payload if isinstance(item, dict)]

    def get_device_details(self) -> dict[str, Any]:
        try:
            result = self._request("get_device_details")
        except Exception:
            return {}
        payload = result.get("device") if isinstance(result.get("device"), dict) else result
        return dict(payload) if isinstance(payload, dict) else {}

    def send_directed_private_message(
        self,
        *,
        recipient_public_key: str,
        text: str,
        sender_identity: dict[str, Any] | None = None,
        destination_identity: dict[str, Any] | None = None,
        delivery_id: str | None = None,
        reply_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            result = self._request(
                "send_directed_private_message",
                params={
                    "recipient_public_key": str(recipient_public_key or "").strip(),
                    "text": str(text or ""),
                    "sender_identity": dict(sender_identity or {}),
                    "destination_identity": dict(destination_identity or {}),
                    "delivery_id": str(delivery_id or "").strip() or None,
                    "reply_context": dict(reply_context or {}),
                },
            )
        except Exception as exc:
            return False, f"MeshCore gateway link directed private send failed: {exc}", None
        accepted = bool(result.get("accepted"))
        message = str(result.get("message") or ("Directed private message accepted by gateway link." if accepted else "Directed private message rejected by gateway link.")).strip()
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
        return accepted, message, payload

    def send_channel_message(
        self,
        *,
        channel_index: int,
        text: str,
        channel_name: str | None = None,
        delivery_id: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            result = self._request(
                "send_channel_message",
                params={
                    "channel_index": int(channel_index),
                    "text": str(text or ""),
                    "channel_name": str(channel_name or "").strip() or None,
                    "delivery_id": str(delivery_id or "").strip() or None,
                },
            )
        except Exception as exc:
            return False, f"MeshCore gateway link channel send failed: {exc}", None
        accepted = bool(result.get("accepted"))
        message = str(result.get("message") or ("Channel message accepted by gateway link." if accepted else "Channel message rejected by gateway link.")).strip()
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else None
        return accepted, message, payload

    def set_tx_power(self, *, tx_power_dbm: int) -> tuple[bool, str, dict[str, Any] | None]:
        try:
            result = self._request("set_tx_power", params={"tx_power_dbm": int(tx_power_dbm)})
        except Exception as exc:
            return False, f"MeshCore gateway link TX power update failed: {exc}", None
        accepted = bool(result.get("accepted", True))
        message = str(result.get("message") or (f"TX power set to {tx_power_dbm} dBm." if accepted else "TX power update rejected by gateway link.")).strip()
        payload = dict(result) if isinstance(result, dict) else None
        return accepted, message, payload

    def build_request_envelope(self, *, request_id: str, method: str, params: dict[str, Any] | None = None) -> GatewayLinkEnvelope:
        return GatewayLinkEnvelope(
            version=self.protocol_version,
            type="request",
            id=str(request_id).strip(),
            method=str(method).strip(),
            params=dict(params or {}),
        )

    def _request(self, method: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = f"gw-{uuid4()}"
        envelope = self.build_request_envelope(request_id=request_id, method=method, params=params)
        response = self._exchange(envelope)
        if response.type != "response":
            raise RuntimeError(f"Gateway link method {method} returned unexpected envelope type {response.type!r}.")
        if response.id != request_id:
            raise RuntimeError(f"Gateway link method {method} returned mismatched response id {response.id!r}.")
        if not response.ok:
            error_payload = response.error if isinstance(response.error, dict) else {}
            raise RuntimeError(str(error_payload.get("message") or f"Gateway link method {method} failed."))
        return dict(response.result or {})

    def _exchange(self, envelope: GatewayLinkEnvelope) -> GatewayLinkEnvelope:
        host, port = self._resolve_tcp_endpoint()
        with socket.create_connection((host, port), timeout=self._settings.timeout_seconds) as connection:
            connection.sendall(encode_gateway_link_frame(envelope))
            return self._recv_envelope(connection)

    def _resolve_tcp_endpoint(self) -> tuple[str, int]:
        normalized_endpoint = self._endpoint
        if self._carrier not in {"tcp", "stream"}:
            raise RuntimeError(f"Unsupported stream carrier {self._carrier!r}.")
        if normalized_endpoint.startswith("tcp://"):
            normalized_endpoint = normalized_endpoint[6:]
        host, separator, port_text = normalized_endpoint.rpartition(":")
        if not separator or not host or not port_text:
            raise RuntimeError(f"TCP gateway-link endpoint must look like host:port, got {self._endpoint!r}.")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise RuntimeError(f"TCP gateway-link endpoint has invalid port {port_text!r}.") from exc
        if port <= 0 or port > 65535:
            raise RuntimeError(f"TCP gateway-link endpoint has invalid port {port_text!r}.")
        return host, port

    def _recv_envelope(self, connection: socket.socket) -> GatewayLinkEnvelope:
        header = self._recv_exact(connection, 4)
        payload_length = int.from_bytes(header, byteorder="big", signed=False)
        payload = self._recv_exact(connection, payload_length)
        return decode_gateway_link_frame(header + payload)

    def _recv_exact(self, connection: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = int(size)
        while remaining > 0:
            chunk = connection.recv(remaining)
            if not chunk:
                raise RuntimeError("Gateway link closed before the frame completed.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class HttpMeshCoreGatewayLink:
    def __init__(self, settings: MeshCoreRuntimeSettings) -> None:
        self._settings = settings
        self._channels: list[dict[str, Any]] = []
        self._observed_nodes: list[dict[str, Any]] = []
        self._snapshot: dict[str, Any] = {
            "connected": False,
            "status": self._initial_status(),
            "gateway_id": settings.gateway_id,
            "display_name": settings.display_name,
            "contract_version": settings.contract_version,
            "base_url": settings.base_url,
            "identity": {},
            "runtime_status": {},
            "last_error": None,
        }

    @property
    def gateway_id(self) -> str:
        return self._settings.gateway_id

    def get_snapshot(self) -> dict[str, Any]:
        self._poll_once()
        return dict(self._snapshot)

    def list_channels(self) -> list[dict[str, Any]]:
        self._poll_once()
        return [dict(channel) for channel in self._channels]

    def list_observed_nodes(self) -> list[dict[str, Any]]:
        self._poll_once()
        return [dict(node) for node in self._observed_nodes]

    def list_gateway_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]:
        if not self._settings.enabled or not self._settings.base_url:
            return []
        query = f"?limit={max(1, int(limit))}"
        if cursor:
            query += f"&cursor={parse.quote(str(cursor), safe='')}"
        payload = self._fetch_json_optional("/api/inbound-events" + query)
        events = payload.get("events") if isinstance(payload.get("events"), list) else None
        if events is None:
            fallback = self._fetch_json_optional("/api/messages" + query)
            events = fallback.get("messages") if isinstance(fallback.get("messages"), list) else []
        return [dict(event) for event in events if isinstance(event, dict)]

    def list_gateway_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict[str, Any]]:
        if not self._settings.enabled or not self._settings.base_url:
            return []
        query = f"?limit={max(1, int(limit))}"
        if state:
            query += f"&state={parse.quote(str(state), safe='')}"
        payload = self._fetch_json_optional("/api/outbound-requests" + query)
        requests_payload = payload.get("requests") if isinstance(payload.get("requests"), list) else []
        return [dict(item) for item in requests_payload if isinstance(item, dict)]

    def get_device_details(self) -> dict[str, Any]:
        snapshot = self.get_snapshot()
        device_payload = self._fetch_json_optional("/api/device")
        if device_payload:
            return dict(device_payload)
        runtime_status = snapshot.get("runtime_status") if isinstance(snapshot.get("runtime_status"), dict) else {}
        return {
            "identity": dict(snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}),
            "runtime_name": snapshot.get("runtime_name"),
            "runtime_version": snapshot.get("runtime_version"),
            "contract_version": snapshot.get("contract_version"),
            "region": snapshot.get("region"),
            "transport": runtime_status.get("transport"),
            "runtime_status": runtime_status,
        }

    def set_tx_power(self, *, tx_power_dbm: int) -> tuple[bool, str, dict[str, Any] | None]:
        return False, "MeshCore HTTP compatibility link does not support TX power updates.", None

    def send_directed_private_message(
        self,
        *,
        recipient_public_key: str,
        text: str,
        sender_identity: dict[str, Any] | None = None,
        destination_identity: dict[str, Any] | None = None,
        delivery_id: str | None = None,
        reply_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        snapshot = self.get_snapshot()
        if not snapshot.get("connected"):
            return False, "MeshCore runtime is not connected.", None

        normalized_public_key = str(recipient_public_key or "").strip()
        if not normalized_public_key:
            return False, "MeshCore directed private delivery requires a recipient public key.", None
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return False, "MeshCore directed private delivery requires message text.", None

        request_payload = {
            "recipient_public_key": normalized_public_key,
            "text": normalized_text,
            "delivery_id": str(delivery_id or "").strip() or None,
            "sender_identity": dict(sender_identity or {}),
            "destination_identity": dict(destination_identity or {}),
            "reply_context": dict(reply_context or {}),
        }
        try:
            payload = self._request_json("/directed_private_tx", method="POST", json_body=request_payload)
        except error.HTTPError as exc:
            if exc.code == 404:
                payload = self._enqueue_outbound_request_via_operational_ui(
                    message_class="directed_private",
                    payload={"text": normalized_text},
                    destination={"public_key": normalized_public_key, "native_address": normalized_public_key},
                    fallback_path="/api/contacts/send",
                    fallback_body={"pub_key": normalized_public_key, "text": normalized_text},
                )
                accepted = bool(payload.get("ok"))
                response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else payload
                status_payload = payload.get("status") if isinstance(payload.get("status"), dict) else {}
                event = response_payload.get("message") if isinstance(response_payload.get("message"), dict) else {}
                normalized_payload = {
                    "accepted": accepted,
                    "status": str(status_payload.get("state") or ("sent" if accepted else "failed")),
                    "request_id": payload.get("request", {}).get("request_id") if isinstance(payload.get("request"), dict) else None,
                    "message_id": str(status_payload.get("transport_message_id") or event.get("event_utc") or event.get("recipient_prefix") or uuid4()),
                    "recipient_public_key": normalized_public_key,
                    "text": normalized_text,
                    "observed_at": status_payload.get("updated_at") or event.get("event_utc"),
                    "raw": payload,
                }
                message = "Directed private message accepted by MeshCore operational UI." if accepted else str(payload.get("error") or status_payload.get("warning") or payload.get("warning") or "MeshCore operational UI rejected the directed private message.")
                return accepted, message, normalized_payload
            self._set_unreachable(str(exc))
            return False, f"MeshCore directed private send failed: {exc}", None
        except Exception as exc:
            self._set_unreachable(str(exc))
            return False, f"MeshCore directed private send failed: {exc}", None

        accepted = bool(payload.get("accepted") or payload.get("ok"))
        message = "Directed private message accepted by MeshCore runtime." if accepted else str(payload.get("warning") or "MeshCore runtime rejected the directed private message.")
        return accepted, message, payload

    def send_channel_message(
        self,
        *,
        channel_index: int,
        text: str,
        channel_name: str | None = None,
        delivery_id: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        snapshot = self.get_snapshot()
        if not snapshot.get("connected"):
            return False, "MeshCore runtime is not connected.", None

        normalized_text = str(text or "").strip()
        if not normalized_text:
            return False, "MeshCore private-channel delivery requires message text.", None

        normalized_channel_index = int(channel_index)
        request_payload = {
            "channel_index": normalized_channel_index,
            "channel_name": str(channel_name or "").strip() or None,
            "text": normalized_text,
            "delivery_id": str(delivery_id or "").strip() or None,
        }
        try:
            payload = self._request_json("/public_group_tx", method="POST", json_body=request_payload)
        except error.HTTPError as exc:
            if exc.code == 404:
                payload = self._enqueue_outbound_request_via_operational_ui(
                    message_class="group_message",
                    payload={"text": normalized_text},
                    destination={
                        "channel_index": normalized_channel_index,
                        "channel_name": str(channel_name or "").strip() or None,
                        "native_address": str(channel_name or normalized_channel_index),
                    },
                    fallback_path="/public_group_tx",
                    fallback_body=request_payload,
                )
                accepted = bool(payload.get("ok"))
                response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else payload
                status_payload = payload.get("status") if isinstance(payload.get("status"), dict) else {}
                event = response_payload.get("message") if isinstance(response_payload.get("message"), dict) else {}
                normalized_payload = {
                    "accepted": accepted,
                    "status": str(status_payload.get("state") or ("sent" if accepted else "failed")),
                    "request_id": payload.get("request", {}).get("request_id") if isinstance(payload.get("request"), dict) else None,
                    "message_id": str(status_payload.get("transport_message_id") or event.get("event_utc") or uuid4()),
                    "channel_index": normalized_channel_index,
                    "channel_name": str(channel_name or "").strip() or None,
                    "text": normalized_text,
                    "observed_at": status_payload.get("updated_at") or event.get("event_utc"),
                    "raw": payload,
                }
                message = "MeshCore private-channel message accepted by operational UI." if accepted else str(payload.get("error") or status_payload.get("warning") or payload.get("warning") or "MeshCore operational UI rejected the channel message.")
                return accepted, message, normalized_payload
            self._set_unreachable(str(exc))
            return False, f"MeshCore private-channel send failed: {exc}", None
        except Exception as exc:
            self._set_unreachable(str(exc))
            return False, f"MeshCore private-channel send failed: {exc}", None

        accepted = bool(payload.get("accepted") or payload.get("ok"))
        message = "MeshCore private-channel message accepted by runtime." if accepted else str(payload.get("warning") or "MeshCore runtime rejected the channel message.")
        return accepted, message, payload

    def _initial_status(self) -> str:
        if not self._settings.enabled:
            return "disabled"
        return "unconfigured" if not self._settings.base_url else "starting"

    def _poll_once(self) -> None:
        if not self._settings.enabled:
            self._snapshot.update({"connected": False, "status": "disabled", "last_error": None})
            return
        if not self._settings.base_url:
            self._snapshot.update(
                {
                    "connected": False,
                    "status": "unconfigured",
                    "last_error": "MeshCore runtime base URL is not configured.",
                }
            )
            return
        try:
            status_payload = self._request_json("/status", method="GET")
        except error.HTTPError as exc:
            if exc.code == 404:
                self._poll_operational_ui_once()
                return
            self._set_unreachable(str(exc))
            return
        except Exception as exc:
            self._set_unreachable(str(exc))
            return

        try:
            identity_payload = self._request_json("/identity", method="GET")
            neighbors_payload = self._request_json("/neighbors", method="GET")
            channels_payload = self._request_json("/channels", method="GET")
        except Exception as exc:
            self._set_unreachable(str(exc))
            return

        runtime = status_payload.get("runtime") if isinstance(status_payload.get("runtime"), dict) else {}
        runtime_contract_version = str(runtime.get("contract_version") or "").strip() or None
        configured_contract_version = str(self._settings.contract_version or "").strip() or None
        compatibility_error = None
        if configured_contract_version and runtime_contract_version:
            configured_major = configured_contract_version.split(".", 1)[0]
            runtime_major = runtime_contract_version.split(".", 1)[0]
            if configured_major != runtime_major:
                compatibility_error = (
                    f"MeshCore contract major version mismatch: runtime {runtime_contract_version}, basestation expects {configured_contract_version}."
                )

        connected = bool(status_payload.get("connected")) and compatibility_error is None
        status_text = str(status_payload.get("status") or "connected").strip() or "connected"
        if compatibility_error is not None:
            status_text = "incompatible"

        self._snapshot.update(
            {
                "connected": connected,
                "status": status_text,
                "region": str(status_payload.get("region") or "").strip() or None,
                "runtime_name": str(runtime.get("name") or "meshcore").strip() or "meshcore",
                "runtime_version": str(runtime.get("version") or "").strip() or None,
                "contract_version": runtime_contract_version or configured_contract_version,
                "identity": identity_payload if isinstance(identity_payload, dict) else {},
                "runtime_status": status_payload if isinstance(status_payload, dict) else {},
                "last_error": compatibility_error,
            }
        )
        self._channels = self._extract_channels(channels_payload)
        self._observed_nodes = self._extract_neighbors(neighbors_payload)

    def _poll_operational_ui_once(self) -> None:
        try:
            runtime_payload = self._request_json("/api/runtime", method="GET")
            contacts_payload = self._fetch_json_optional("/api/contacts")
        except Exception as exc:
            self._set_unreachable(str(exc))
            return

        interface_mode = str(runtime_payload.get("interface_mode") or "").strip().lower() or None
        service = runtime_payload.get("service") if isinstance(runtime_payload.get("service"), dict) else {}
        status_info = runtime_payload.get("status") if isinstance(runtime_payload.get("status"), dict) else {}
        parsed_status = status_info.get("parsed") if isinstance(status_info.get("parsed"), dict) else {}
        host = runtime_payload.get("host") if isinstance(runtime_payload.get("host"), dict) else {}
        transport_ready = bool(service.get("active")) and bool(
            parsed_status.get("live_runtime_ready")
            or parsed_status.get("transport_started")
            or parsed_status.get("bridge_ready")
        )
        connected = transport_ready and interface_mode != "app"
        status_text = "runtime active"
        if interface_mode == "app":
            status_text = "app mode passive"
        elif interface_mode:
            status_text = f"{interface_mode} mode active"
        if not transport_ready:
            status_text = str(service.get("SubState") or service.get("ActiveState") or "unreachable").strip().lower() or "unreachable"

        self._snapshot.update(
            {
                "connected": connected,
                "status": status_text,
                "region": None,
                "runtime_name": "meshcore-pi-node-ui",
                "runtime_version": None,
                "contract_version": self._settings.contract_version,
                "identity": {
                    "local_id": str(host.get("hostname") or self.gateway_id),
                    "display_name": str(host.get("hostname") or self.gateway_id),
                    "public_key": None,
                    "identity_loaded": connected,
                },
                "runtime_status": {
                    "transport": "meshcore-runtime",
                    "source_kind": "meshcore-pi-node-ui",
                    "status": status_text,
                    "interface_mode": interface_mode,
                    "helper_tx_allowed": interface_mode == "web",
                    "gateway_tx_allowed": interface_mode == "web",
                    "browser_send_allowed": interface_mode == "web",
                    "transport_ready": transport_ready,
                    "runtime_endpoint": parsed_status.get("runtime_endpoint"),
                    "service": service,
                    "status_file": status_info,
                    **parsed_status,
                },
                "last_error": None,
            }
        )
        self._channels = [
            {
                "index": 0,
                "name": "Primary Public",
                "role": "browser",
                "visibility": "public",
                "browser_send_allowed": interface_mode == "web",
            }
        ]
        self._observed_nodes = self._map_operational_ui_contacts_to_neighbors(contacts_payload)

    def _extract_channels(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        channels = payload.get("channels") if isinstance(payload.get("channels"), list) else []
        return [dict(channel) for channel in channels if isinstance(channel, dict)]

    def _extract_neighbors(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        neighbors = payload.get("neighbors") if isinstance(payload.get("neighbors"), list) else []
        rows = [dict(item) for item in neighbors if isinstance(item, dict)]
        rows.sort(key=lambda item: str(item.get("last_seen_at") or item.get("last_observation_utc") or ""), reverse=True)
        return rows

    def _map_operational_ui_contacts_to_neighbors(self, contacts_payload: dict[str, Any]) -> list[dict[str, Any]]:
        contacts = contacts_payload.get("contacts") if isinstance(contacts_payload.get("contacts"), list) else contacts_payload.get("recent_contacts")
        if not isinstance(contacts, list):
            return []
        neighbors: list[dict[str, Any]] = []
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            public_key = str(contact.get("pub_key") or "").strip() or None
            pub_key_prefix = str(contact.get("pub_key_prefix") or "").strip() or (public_key[:12] if public_key else "")
            node_id = f"meshcore:{pub_key_prefix}" if pub_key_prefix else None
            if not node_id:
                continue
            neighbors.append(
                {
                    "node_id": node_id,
                    "public_key": public_key,
                    "contact_name": contact.get("name"),
                    "display_name": contact.get("name"),
                    "short_name": contact.get("name"),
                    "last_seen_at": contact.get("last_advert_utc") or contact.get("lastmod_utc"),
                    "channel_name": "Primary Public",
                    "presence_state": "MESH_ACTIVE",
                    "rssi": None,
                    "snr": None,
                }
            )
        neighbors.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
        return neighbors

    def _enqueue_outbound_request_via_operational_ui(
        self,
        *,
        message_class: str,
        payload: dict[str, Any],
        destination: dict[str, Any],
        fallback_path: str,
        fallback_body: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._request_json(
                "/api/outbound-requests",
                method="POST",
                json_body={
                    "gateway_id": self.gateway_id,
                    "network": "meshcore",
                    "message_class": message_class,
                    "destination": dict(destination),
                    "payload": dict(payload),
                },
            )
        except error.HTTPError as exc:
            if exc.code != 404:
                raise
        return self._request_json(fallback_path, method="POST", json_body=dict(fallback_body))

    def _fetch_json_optional(self, path: str) -> dict[str, Any]:
        try:
            return self._request_json(path, method="GET")
        except error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise

    def _request_json(self, path: str, *, method: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = parse.urljoin(self._settings.base_url.rstrip("/") + "/", path.lstrip("/"))
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        http_request = request.Request(url, data=body, method=method)
        http_request.add_header("Accept", "application/json")
        if json_body is not None:
            http_request.add_header("Content-Type", "application/json")
        if self._settings.auth_token:
            http_request.add_header("Authorization", f"Bearer {self._settings.auth_token}")
        with request.urlopen(http_request, timeout=self._settings.timeout_seconds) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise RuntimeError(f"MeshCore runtime at {url} returned a non-object JSON payload.")
        return data

    def _set_unreachable(self, message: str) -> None:
        self._snapshot.update(
            {
                "connected": False,
                "status": "unreachable",
                "last_error": message,
            }
        )