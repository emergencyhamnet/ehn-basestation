from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import re
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Any, Callable

from ehn_basestation.core.events import utc_now_iso

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


class MeshtasticGatewayBroker:
    def __init__(
        self,
        *,
        gateway_id: str,
        display_name: str,
        serial_port: str,
        group_channel_index: int = 1,
        interface_factory: Callable[[str], Any] | None = None,
        publisher: Any | None = None,
        reconnect_seconds: float = 5.0,
    ) -> None:
        self.gateway_id = str(gateway_id).strip()
        self.display_name = str(display_name).strip() or self.gateway_id
        self.serial_port = str(serial_port).strip()
        self.group_channel_index = max(0, int(group_channel_index))
        self._interface_factory = interface_factory
        self._publisher = publisher
        self._reconnect_seconds = max(0.1, float(reconnect_seconds))
        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._interface: Any | None = None
        self._connected = False
        self._status = "stopped"
        self._error: str | None = None
        self._event_sequence = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=250)
        self._inbound: deque[dict[str, Any]] = deque(maxlen=250)
        self._logs: deque[dict[str, str]] = deque(maxlen=100)
        self._live_nodes: dict[str, dict[str, Any]] = {}
        self._seen_packet_ids: set[str] = set()
        self._seen_packet_order: deque[str] = deque(maxlen=500)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._status = "connecting"
            self._thread = Thread(target=self._run, name=f"meshtastic-{self.gateway_id}", daemon=True)
            self._thread.start()

    def stop(self, *, timeout: float = 3.0) -> None:
        self._stop_event.set()
        with self._lock:
            interface = self._interface
        if interface is not None:
            try:
                interface.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(0.0, timeout))
        self._unsubscribe()
        with self._lock:
            self._interface = None
            self._connected = False
            self._status = "stopped"

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            interface = self._interface
            node_id, node_name, short_name = self._local_identity(interface)
            return {
                "gateway_id": self.gateway_id,
                "display_name": self.display_name,
                "gateway_type": "mesh",
                "network": "meshtastic",
                "transport": "meshtastic-serial",
                "serial_port": self.serial_port,
                "connected": self._connected,
                "status": self._status,
                "error": self._error,
                "node_id": node_id,
                "node_name": node_name,
                "short_name": short_name,
                "group_channel_index": self.group_channel_index,
                "group_channel_ready": self._connected,
                "last_event_at": self._events[-1].get("event_utc") if self._events else None,
            }

    def list_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in list(self._events)[-max(1, int(limit)):]]

    def get_details(self, *, node_limit: int = 100, log_limit: int = 30) -> dict[str, Any]:
        with self._lock:
            interface = self._interface
            node_id, node_name, short_name = self._local_identity(interface)
            metadata = self._device_metadata(interface)
            channels = self._local_channels(interface)
            nodes_by_id = {node["node_id"]: node for node in self._observed_nodes(interface, limit=node_limit)}
            for node_id, live_node in self._live_nodes.items():
                nodes_by_id[node_id] = {**nodes_by_id.get(node_id, {}), **live_node}
            channel_names = {channel["index"]: channel["name"] for channel in channels if channel.get("index") is not None}
            nodes = list(nodes_by_id.values())
            for node in nodes:
                channel_index = node.get("last_channel_index")
                node["last_channel_name"] = channel_names.get(channel_index) if channel_index is not None else None
            nodes.sort(key=lambda item: item.get("last_heard") or "", reverse=True)
            total_node_count = len(nodes)
            nodes = nodes[:max(1, int(node_limit))]
            return {
                "runtime": {
                    "transport": "meshtastic-serial",
                    "serial_port": self.serial_port,
                    "connected": self._connected,
                    "status": self._status,
                    "worker_running": bool(self._thread and self._thread.is_alive()),
                    "group_channel_index": self.group_channel_index,
                    "node_inventory_mode": "live_packets_and_cached_node_table",
                },
                "device": {
                    "node_id": node_id,
                    "long_name": node_name,
                    "short_name": short_name,
                    **metadata,
                },
                "channels": channels,
                "observed_nodes": nodes,
                "observed_node_count": total_node_count,
                "logs": [dict(entry) for entry in list(self._logs)[-max(1, int(log_limit)):]],
            }

    def drain_inbound_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            count = min(max(1, int(limit)), len(self._inbound))
            return [self._inbound.popleft() for _ in range(count)]

    def send_text(
        self,
        *,
        text: str,
        destination_id: str = "^all",
        channel_index: int | None = None,
        want_ack: bool = False,
    ) -> dict[str, Any]:
        clean_text = str(text or "").strip()
        clean_destination = str(destination_id or "^all").strip()
        index = self.group_channel_index if channel_index is None else max(0, int(channel_index))
        if not clean_text:
            return {"accepted": False, "status": "rejected", "detail": "text is required"}
        if not clean_destination:
            return {"accepted": False, "status": "rejected", "detail": "destination is required"}

        with self._lock:
            interface = self._interface if self._connected else None
            if interface is None:
                return {"accepted": False, "status": "unavailable", "detail": "Meshtastic gateway is not connected"}

            event: dict[str, Any] = {
                "event_type": "text_tx",
                "direction": "sent",
                "network": "meshtastic",
                "gateway_id": self.gateway_id,
                "destination_id": clean_destination,
                "channel_index": index,
                "text": clean_text,
                "status": "accepted_by_local_gateway",
                "event_utc": utc_now_iso(),
            }

            def onAckNak(response: dict[str, Any]) -> None:
                decoded = response.get("decoded") if isinstance(response, dict) and isinstance(response.get("decoded"), dict) else {}
                routing = decoded.get("routing") if isinstance(decoded.get("routing"), dict) else None
                if routing is None:
                    event["status"] = "response_received"
                else:
                    error_reason = str(routing.get("errorReason") or "NONE").upper()
                    event["status"] = "acknowledged" if error_reason == "NONE" else "nak"
                event["response"] = {
                    "packet_id": response.get("id") if isinstance(response, dict) else None,
                    "from_id": str(response.get("fromId") or "") or None if isinstance(response, dict) else None,
                    "to_id": str(response.get("toId") or "") or None if isinstance(response, dict) else None,
                    "request_id": decoded.get("requestId"),
                    "error_reason": str(routing.get("errorReason") or "NONE") if routing is not None else None,
                }
                self._append_event(event)

            try:
                packet = interface.sendText(
                    clean_text,
                    destinationId=clean_destination,
                    channelIndex=index,
                    wantAck=bool(want_ack),
                    onResponse=onAckNak if want_ack else None,
                )
            except Exception as exc:
                return {"accepted": False, "status": "failed", "detail": str(exc)}

            packet_id = getattr(packet, "id", None)
            if isinstance(packet, dict):
                packet_id = packet.get("id", packet_id)
            event["transport_message_id"] = str(packet_id) if packet_id is not None else None
            self._append_event(event)

        return {
            "accepted": True,
            "status": "accepted_by_local_gateway",
            "detail": "Message accepted by the local Meshtastic client; RF delivery is not confirmed.",
            "transport_message_id": event["transport_message_id"],
        }

    def _run(self) -> None:
        try:
            publisher = self._load_publisher()
            self._publisher = publisher
            publisher.subscribe(self._on_receive, "meshtastic.receive")
            publisher.subscribe(self._on_connection_established, "meshtastic.connection.established")
            publisher.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
            publisher.subscribe(self._on_log_line, "meshtastic.log.line")
        except Exception as exc:
            with self._lock:
                self._status = "unavailable"
                self._error = str(exc)
            return

        while not self._stop_event.is_set():
            interface = None
            with self._lock:
                self._connected = False
                self._status = "connecting"
                self._error = None
            try:
                interface = self._create_interface()
                with self._lock:
                    self._interface = interface
                    self._connected = self._interface_connected(interface)
                    self._status = "connected" if self._connected else "connecting"
                while not self._stop_event.wait(0.25):
                    connected = self._interface_connected(interface)
                    with self._lock:
                        self._connected = connected
                        if not connected:
                            self._status = "disconnected"
                    if not connected:
                        break
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)
                    self._status = "disconnected"
            finally:
                if interface is not None:
                    try:
                        interface.close()
                    except Exception:
                        pass
                with self._lock:
                    if self._interface is interface:
                        self._interface = None
                    self._connected = False
            if not self._stop_event.wait(self._reconnect_seconds):
                continue

        with self._lock:
            self._status = "stopped"

    def _create_interface(self) -> Any:
        factory = self._interface_factory
        if factory is None:
            from meshtastic.serial_interface import SerialInterface

            factory = lambda port: SerialInterface(devPath=port, timeout=20, noNodes=False)
            self._interface_factory = factory
        return factory(self.serial_port)

    def _load_publisher(self) -> Any:
        if self._publisher is not None:
            return self._publisher
        from pubsub import pub

        return pub

    def _unsubscribe(self) -> None:
        publisher = self._publisher
        if publisher is None:
            return
        for listener, topic in (
            (self._on_receive, "meshtastic.receive"),
            (self._on_connection_established, "meshtastic.connection.established"),
            (self._on_connection_lost, "meshtastic.connection.lost"),
            (self._on_log_line, "meshtastic.log.line"),
        ):
            try:
                publisher.unsubscribe(listener, topic)
            except Exception:
                pass

    def _on_connection_established(self, *, interface: Any) -> None:
        with self._lock:
            if interface is self._interface:
                self._connected = True
                self._status = "connected"
                self._error = None

    def _on_connection_lost(self, *, interface: Any) -> None:
        with self._lock:
            if interface is self._interface:
                self._connected = False
                self._status = "disconnected"

    def _on_receive(self, *, packet: dict[str, Any], interface: Any) -> None:
        if not isinstance(packet, dict):
            return
        decoded = packet.get("decoded") if isinstance(packet.get("decoded"), dict) else {}
        text = str(decoded.get("text") or "").strip()
        sender_id = str(packet.get("fromId") or "").strip()
        if not sender_id:
            sender_number = _safe_int(packet.get("from"))
            if sender_number is not None and 0 < sender_number < 0xFFFFFFFF:
                sender_id = f"!{sender_number:08x}"
        if not sender_id and not text:
            return
        packet_id = str(packet.get("id") or "").strip()
        with self._lock:
            if packet_id and packet_id in self._seen_packet_ids:
                return
            if packet_id:
                if len(self._seen_packet_order) == self._seen_packet_order.maxlen:
                    expired = self._seen_packet_order.popleft()
                    self._seen_packet_ids.discard(expired)
                self._seen_packet_order.append(packet_id)
                self._seen_packet_ids.add(packet_id)
            channel_index = _safe_int(packet.get("channel"))
            if sender_id:
                known_nodes = getattr(interface, "nodes", None)
                radio_node = known_nodes.get(sender_id) if isinstance(known_nodes, dict) else None
                self._remember_live_node(
                    sender_id,
                    radio_node if isinstance(radio_node, dict) else {},
                    packet=packet,
                    channel_index=channel_index,
                )
            destination = str(packet.get("toId") or "").strip()
            broadcast = destination in {"^all", "!ffffffff"} or _safe_int(packet.get("to")) == 0xFFFFFFFF
            is_group = broadcast and channel_index == self.group_channel_index
            portnum = str(decoded.get("portnum") or packet.get("portnum") or "unknown").strip()
            packet_kind = "text" if text else portnum.casefold()
            live_node = self._live_nodes.get(sender_id, {})
            event = {
                "event_type": "text_rx" if text else "packet_rx",
                "direction": "received",
                "network": "meshtastic",
                "gateway_id": self.gateway_id,
                "sender_endpoint": sender_id or None,
                "source_endpoint": sender_id or None,
                "sender_display_name": (
                    _sender_name(packet) or live_node.get("long_name") or live_node.get("short_name")
                ),
                "destination_id": destination or None,
                "channel_index": channel_index,
                "message_class": ("channel_message" if broadcast else "direct_message") if text else packet_kind,
                "is_ehn_group_channel": is_group,
                "text": text or f"Received {portnum} mesh packet",
                "transport_message_id": packet_id or None,
                "event_utc": utc_now_iso(),
            }
            self._append_event(event)
            if text and sender_id and (not broadcast or is_group):
                self._inbound.append(dict(event))

    def _on_log_line(self, *, line: str, interface: Any) -> None:
        del interface
        clean_line = _ANSI_ESCAPE_RE.sub("", str(line or "")).strip()[:500]
        if not clean_line:
            return
        with self._lock:
            self._logs.append({"event_utc": utc_now_iso(), "line": clean_line})

    def _remember_live_node(
        self,
        node_id: str,
        radio_node: dict[str, Any],
        *,
        packet: dict[str, Any],
        channel_index: int | None,
    ) -> None:
        radio_user = radio_node.get("user") if isinstance(radio_node.get("user"), dict) else {}
        packet_user = packet.get("user") if isinstance(packet.get("user"), dict) else {}
        previous_node = self._live_nodes.get(node_id, {})
        metrics = radio_node.get("deviceMetrics") if isinstance(radio_node.get("deviceMetrics"), dict) else {}
        entry = {
            "node_id": node_id,
            "long_name": (
                _string_or_none(packet_user.get("longName"))
                or _string_or_none(radio_user.get("longName"))
                or previous_node.get("long_name")
            ),
            "short_name": (
                _string_or_none(packet_user.get("shortName"))
                or _string_or_none(radio_user.get("shortName"))
                or previous_node.get("short_name")
            ),
            "hardware_model": _string_or_none(radio_user.get("hwModel")),
            "role": _string_or_none(radio_user.get("role")),
            "last_heard": utc_now_iso(),
            "presence_state": "MESH_ACTIVE",
            "last_channel_index": channel_index,
            "last_channel_name": None,
            "last_rssi": _number_or_none(packet.get("rxRssi")) or _number_or_none(radio_node.get("lastRxRssi")),
            "last_snr": _number_or_none(packet.get("rxSnr")) or _number_or_none(radio_node.get("snr")),
            "hops_away": _safe_int(radio_node.get("hopsAway")),
            "battery_level": _safe_int(metrics.get("batteryLevel")),
            "channel_utilization": _number_or_none(metrics.get("channelUtilization")),
        }
        with self._lock:
            self._live_nodes[node_id] = {**self._live_nodes.get(node_id, {}), **entry}
            while len(self._live_nodes) > 2000:
                self._live_nodes.pop(next(iter(self._live_nodes)))

    def _append_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._event_sequence += 1
            event["event_id"] = str(self._event_sequence)
            self._events.append(dict(event))

    @staticmethod
    def _interface_connected(interface: Any) -> bool:
        connected = getattr(interface, "isConnected", None)
        return bool(connected.is_set()) if hasattr(connected, "is_set") else bool(connected)

    @staticmethod
    def _observed_nodes(interface: Any | None, *, limit: int) -> list[dict[str, Any]]:
        raw_nodes = getattr(interface, "nodes", None) if interface is not None else None
        if not isinstance(raw_nodes, dict):
            return []
        rows: list[dict[str, Any]] = []
        for node in raw_nodes.values():
            if not isinstance(node, dict):
                continue
            user = node.get("user") if isinstance(node.get("user"), dict) else {}
            metrics = node.get("deviceMetrics") if isinstance(node.get("deviceMetrics"), dict) else {}
            node_id = str(user.get("id") or "").strip()
            if not node_id:
                number = _safe_int(node.get("num"))
                node_id = f"!{number:08x}" if number is not None and number >= 0 else ""
            if not node_id:
                continue
            rows.append({
                "node_id": node_id,
                "long_name": _string_or_none(user.get("longName")),
                "short_name": _string_or_none(user.get("shortName")),
                "hardware_model": _string_or_none(user.get("hwModel")),
                "role": _string_or_none(user.get("role")),
                "last_heard": _unix_time_iso(node.get("lastHeard")),
                "presence_state": "MESH_ACTIVE" if _safe_int(node.get("lastHeard")) else "MESH_STALE",
                "last_channel_index": _safe_int(node.get("channel")),
                "last_channel_name": None,
                "last_rssi": _number_or_none(node.get("lastRxRssi") or node.get("rxRssi")),
                "snr": _number_or_none(node.get("snr")),
                "hops_away": _safe_int(node.get("hopsAway")),
                "battery_level": _safe_int(metrics.get("batteryLevel")),
                "channel_utilization": _number_or_none(metrics.get("channelUtilization")),
            })
        rows.sort(key=lambda item: item.get("last_heard") or "", reverse=True)
        return rows[:max(1, int(limit))]

    @staticmethod
    def _device_metadata(interface: Any | None) -> dict[str, Any]:
        metadata = getattr(interface, "metadata", None) if interface is not None else None
        if metadata is None:
            return {}
        hardware_model = _safe_int(getattr(metadata, "hw_model", None))
        return {
            "firmware_version": _string_or_none(getattr(metadata, "firmware_version", None)),
            "hardware_model_id": hardware_model,
            "device_state_version": _safe_int(getattr(metadata, "device_state_version", None)),
            "can_shutdown": bool(getattr(metadata, "canShutdown", False)),
            "has_wifi": bool(getattr(metadata, "hasWifi", False)),
            "has_bluetooth": bool(getattr(metadata, "hasBluetooth", False)),
            "has_ethernet": bool(getattr(metadata, "hasEthernet", False)),
        }

    @staticmethod
    def _local_channels(interface: Any | None) -> list[dict[str, Any]]:
        local_node = getattr(interface, "localNode", None) if interface is not None else None
        channels = getattr(local_node, "channels", None) if local_node is not None else None
        if not isinstance(channels, (list, tuple)):
            return []
        rows: list[dict[str, Any]] = []
        for channel in channels:
            settings = getattr(channel, "settings", None)
            index = _safe_int(getattr(channel, "index", None))
            role_value = _safe_int(getattr(channel, "role", None))
            role = {0: "DISABLED", 1: "PRIMARY", 2: "SECONDARY"}.get(role_value, str(role_value) if role_value is not None else None)
            rows.append({
                "index": index,
                "name": _string_or_none(getattr(settings, "name", None)) if settings is not None else None,
                "role": role,
                "uplink_enabled": bool(getattr(settings, "uplink_enabled", False)) if settings is not None else None,
                "downlink_enabled": bool(getattr(settings, "downlink_enabled", False)) if settings is not None else None,
            })
        return rows

    @staticmethod
    def _local_identity(interface: Any | None) -> tuple[str | None, str | None, str | None]:
        if interface is None:
            return None, None, None
        local_node = getattr(interface, "localNode", None)
        node_num = getattr(local_node, "nodeNum", None)
        node_id = f"!{int(node_num):08x}" if isinstance(node_num, int) and node_num >= 0 else None
        try:
            long_name = local_node.getLongName() if local_node is not None else None
        except Exception:
            long_name = None
        try:
            short_name = local_node.getShortName() if local_node is not None else None
        except Exception:
            short_name = None
        return node_id, long_name, short_name


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sender_name(packet: dict[str, Any]) -> str | None:
    user = packet.get("user") if isinstance(packet.get("user"), dict) else {}
    name = str(user.get("longName") or user.get("shortName") or "").strip()
    return name or None


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unix_time_iso(value: Any) -> str | None:
    timestamp = _safe_int(value)
    if timestamp is None or timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None