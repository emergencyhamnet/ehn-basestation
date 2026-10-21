from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import TimeoutError as FutureTimeoutError
from copy import deepcopy
import threading
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ehn_basestation.core.events import utc_now_iso
from ehn_basestation.core.message_policy import prepare_ham_plaintext


class MeshCoreUsbBroker:
    def __init__(
        self, *, serial_port: str, gateway_id: str = "meshcore-usb", baudrate: int = 115200,
        timeout_seconds: float = 3.0, allow_transmit: bool = False,
        sdk_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        if not serial_port.strip() or timeout_seconds <= 0 or baudrate <= 0:
            raise ValueError("Serial port, baudrate and timeout must be valid.")
        self.serial_port = serial_port
        self.gateway_id = gateway_id
        self.baudrate = baudrate
        self.timeout_seconds = timeout_seconds
        self.allow_transmit = allow_transmit
        self._factory = sdk_factory or self._create_sdk
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._command_lock: asyncio.Lock | None = None
        self._client: Any = None
        self._connected = False
        self._lost = False
        self._status = "stopped"
        self._error: str | None = None
        self._self_info: dict[str, Any] = {}
        self._device: dict[str, Any] = {}
        self._battery: dict[str, Any] = {}
        self._channels: list[dict[str, Any]] = []
        self._contacts: dict[str, dict[str, Any]] = {}
        self._contacts_refresh_needed = False
        self._events: deque[dict[str, Any]] = deque(maxlen=250)
        self._outbound: deque[dict[str, Any]] = deque(maxlen=200)
        self._seen: deque[tuple] = deque(maxlen=500)
        self._early_acks: deque[tuple[str, float]] = deque(maxlen=100)
        self._session = uuid4().hex[:12]
        self._event_sequence = 0

    async def _create_sdk(self) -> Any:
        from meshcore import MeshCore

        return await MeshCore.create_serial(
            self.serial_port, baudrate=self.baudrate, default_timeout=self.timeout_seconds,
            only_error=True, auto_reconnect=False, rts=False, dtr=True,
        )

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("USB broker has already been started.")
        self._thread = threading.Thread(target=self._worker, name="meshcore-usb", daemon=True)
        self._thread.start()

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None and self._loop.is_running() and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("USB broker did not stop within five seconds.")

    def _worker(self) -> None:
        asyncio.run(self._run())

    @staticmethod
    def _payload(event: Any, expected: str) -> dict[str, Any]:
        if event is None or event.type.name != expected:
            raise RuntimeError(f"MeshCore did not return {expected}.")
        return dict(event.payload)

    async def _initialize(self, client: Any) -> None:
        device = self._payload(await client.commands.send_device_query(), "DEVICE_INFO")
        battery = self._payload(await client.commands.get_bat(), "BATTERY")
        contacts = self._payload(await client.commands.get_contacts(timeout=max(5, self.timeout_seconds)), "CONTACTS")
        channels = []
        for index in range(max(1, min(int(device.get("max_channels") or 8), 64))):
            result = await client.commands.get_channel(index)
            if result is None or result.type.name != "CHANNEL_INFO":
                continue
            channel = result.payload
            secret = channel.get("channel_secret") or b""
            configured = any(secret) if isinstance(secret, bytes) else any(char != "0" for char in str(secret))
            name = str(channel.get("channel_name") or "")
            channels.append({
                "index": index, "name": name, "is_empty": not name and not configured,
                "secret_preview": "configured" if configured else "-",
                "browser_send_allowed": False, "gateway_send_allowed": self.allow_transmit,
            })
        with self._lock:
            self._self_info = dict(client.self_info)
            self._device = {
                "model": device.get("model"), "version": device.get("ver"),
                "firmware_build": device.get("fw_build"), "firmware_version_code": device.get("fw ver"),
                "max_channels": device.get("max_channels"), "max_contacts": device.get("max_contacts"),
                "path_hash_mode": device.get("path_hash_mode"), "client_repeat": device.get("repeat"),
            }
            self._battery = {"battery_mv": battery.get("level"), "used_kb": battery.get("used_kb"), "total_kb": battery.get("total_kb"), "source": "external_node"}
            self._contacts = {str(key).lower(): dict(value) for key, value in list(contacts.items())[:2000] if isinstance(value, dict)}
            self._contacts_refresh_needed = False
            self._channels = channels
            self._connected = True
            self._status = "connected"
            self._error = None
            self._early_acks.clear()
        self._ready.set()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._command_lock = asyncio.Lock()
        try:
            while not self._stop.is_set():
                client = None
                try:
                    with self._lock:
                        self._status = "connecting"
                        self._connected = False
                    self._ready.clear()
                    self._lost = False
                    client = await self._factory()
                    if client is None:
                        raise ConnectionError("USB companion did not answer.")
                    self._client = client
                    client.subscribe(None, self._on_event)
                    async with self._command_lock:
                        await self._initialize(client)
                    next_refresh = time.monotonic() + 60
                    while not self._stop.is_set() and not self._lost:
                        for _ in range(50):
                            async with self._command_lock:
                                result = await client.commands.get_msg(timeout=self.timeout_seconds)
                            if result is None or (result.type.name == "ERROR" and (result.payload.get("reason") in {"timeout", "no_event_received"} or "error" in result.payload)):
                                raise ConnectionError("USB companion stopped answering message queries.")
                            if result.type.name in {"NO_MORE_MSGS", "ERROR"}:
                                break
                            await asyncio.sleep(0)
                        with self._lock:
                            refresh_contacts = self._contacts_refresh_needed
                        if refresh_contacts:
                            async with self._command_lock:
                                contacts = self._payload(
                                    await client.commands.get_contacts(timeout=max(5, self.timeout_seconds)),
                                    "CONTACTS",
                                )
                            with self._lock:
                                self._contacts = {
                                    str(key).lower(): dict(value)
                                    for key, value in list(contacts.items())[:2000]
                                    if isinstance(value, dict)
                                }
                                self._contacts_refresh_needed = False
                        async with self._command_lock:
                            if time.monotonic() >= next_refresh:
                                battery = self._payload(await client.commands.get_bat(), "BATTERY")
                                contacts = self._payload(await client.commands.get_contacts(timeout=max(5, self.timeout_seconds)), "CONTACTS")
                                with self._lock:
                                    self._battery = {"battery_mv": battery.get("level"), "used_kb": battery.get("used_kb"), "total_kb": battery.get("total_kb"), "source": "external_node"}
                                    self._contacts = {str(key).lower(): dict(value) for key, value in list(contacts.items())[:2000] if isinstance(value, dict)}
                                next_refresh = time.monotonic() + 60
                        await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    with self._lock:
                        self._status = "disconnected"
                        self._error = str(exc)
                finally:
                    with self._lock:
                        self._connected = False
                    self._ready.clear()
                    self._client = None
                    if client is not None:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                if not self._stop.is_set():
                    await asyncio.sleep(2)
        finally:
            with self._lock:
                self._status = "stopped"
                self._connected = False

    def _on_event(self, event: Any) -> None:
        kind = event.type.name
        data = event.payload if isinstance(event.payload, dict) else {}
        if kind == "DISCONNECTED":
            self._lost = True
            self._ready.clear()
            with self._lock:
                self._connected = False
                self._status = "disconnected"
            return
        with self._lock:
            if kind in {"ADVERTISEMENT", "PATH_UPDATE"}:
                self._contacts_refresh_needed = True
                return
            if kind == "ACK":
                code = str(data.get("code") or "")
                if code:
                    self._early_acks.append((code, time.monotonic()))
                    for item in self._outbound:
                        if item.get("expected_ack") == code:
                            item["state"] = "acknowledged"
                            item["updated_at"] = utc_now_iso()
                return
            if kind == "NEW_CONTACT":
                public_key = str(data.get("public_key") or "").lower()
                if len(public_key) == 64 and (public_key in self._contacts or len(self._contacts) < 2000):
                    self._contacts[public_key] = dict(data)
                return
            if kind not in {"CONTACT_MSG_RECV", "CHANNEL_MSG_RECV"}:
                return
            text = str(data.get("text") or "").strip()
            if not text or int(data.get("txt_type") or 0) != 0:
                return
            prefix = str(data.get("pubkey_prefix") or "").lower()
            channel_index = int(data.get("channel_idx", -1))
            fingerprint = (kind, prefix, channel_index, data.get("sender_timestamp"), text)
            if fingerprint in self._seen:
                return
            self._seen.append(fingerprint)
            matches = [key for key in self._contacts if prefix and key.startswith(prefix)]
            sender = matches[0] if len(matches) == 1 else None
            sender_name = ""
            if sender:
                contact = self._contacts.get(sender, {})
                sender_name = str(contact.get("adv_name") or contact.get("name") or contact.get("contact_name") or "").strip()
            self._event_sequence += 1
            payload = {
                "text": text, "channel_index": channel_index,
                "message_scope": "channel" if kind == "CHANNEL_MSG_RECV" else "direct",
                "sender": {"public_key": sender, "pubkey_prefix": prefix, "display_name": sender_name or None},
                "sender_display_name": sender_name or None,
                "snr": data.get("SNR"), "is_plaintext": True,
                "raw": {"sender_timestamp": data.get("sender_timestamp"), "txt_type": 0},
            }
            self._events.append({
                "event_id": f"{self._session}:{self._event_sequence}",
                "event_type": "channel_message_rx" if kind == "CHANNEL_MSG_RECV" else "contact_message_rx",
                "event_ts_utc": utc_now_iso(), "transport": "meshcore-usb-serial",
                "summary": text, "payload": payload,
            })

    def _enrich_inbound_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        sender = dict(payload.get("sender")) if isinstance(payload.get("sender"), dict) else {}
        key = str(sender.get("public_key") or "").lower()
        prefix = str(sender.get("pubkey_prefix") or "").lower()
        if not key and prefix:
            matches = [contact_key for contact_key in self._contacts if contact_key.startswith(prefix)]
            key = matches[0] if len(matches) == 1 else ""
        if key:
            sender["public_key"] = key
            contact = self._contacts.get(key, {})
            name = str(contact.get("adv_name") or contact.get("name") or contact.get("contact_name") or "").strip()
            if name:
                sender["display_name"] = name
                payload["sender_display_name"] = name
            payload["sender"] = sender
            event["payload"] = payload
        return event

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            info = self._self_info
            return deepcopy({
                "connected": self._connected, "status": self._status, "gateway_id": self.gateway_id,
                "display_name": info.get("name") or "External MeshCore", "contract_version": "1.0",
                "identity": {
                    "display_name": info.get("name"), "public_key": info.get("public_key"),
                    "lat": info.get("adv_lat"), "lon": info.get("adv_lon"),
                    "tx_power_dbm": info.get("tx_power"), "tx_power_max_dbm": info.get("max_tx_power"),
                },
                "runtime_status": {
                    "transport": "meshcore-usb-serial", "source_kind": "usb-sdk-broker", "interface_mode": "gateway-link",
                    "serial_port": self.serial_port, "gateway_tx_allowed": self.allow_transmit,
                    "zero_hop_advert_supported": True,
                    "transport_ready": self._connected, "freq_mhz": info.get("radio_freq"),
                    "bw_khz": info.get("radio_bw"), "sf": info.get("radio_sf"), "cr": info.get("radio_cr"),
                    "tx_power_dbm": info.get("tx_power"), "tx_power_max_dbm": info.get("max_tx_power"),
                    "max_contacts": self._device.get("max_contacts"), "max_channels": self._device.get("max_channels"),
                },
                "device_query": self._device, "battery": self._battery, "last_error": self._error,
            })

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "get_snapshot":
            return {"snapshot": self.snapshot()}
        with self._lock:
            if method == "list_channels":
                return {"channels": deepcopy(self._channels)}
            if method == "list_observed_nodes":
                return {"observed_nodes": [
                    {"public_key": key, "node_id": key, "contact_name": contact.get("adv_name"),
                     "last_seen_at": contact.get("last_advert"), "latitude": contact.get("adv_lat"),
                     "longitude": contact.get("adv_lon"), "hops": contact.get("out_path_len")}
                    for key, contact in self._contacts.items()
                ]}
            if method == "list_gateway_inbound_events":
                events = list(self._events)
                cursor = str(params.get("cursor") or "")
                if cursor:
                    positions = [index for index, item in enumerate(events) if item["event_id"] == cursor]
                    if positions:
                        events = events[positions[0] + 1:]
                selected = deepcopy(events[-max(1, min(int(params.get("limit") or 50), 250)):])
                return {"events": [self._enrich_inbound_event(event) for event in selected]}
            if method == "list_gateway_outbound_status":
                items = [item for item in self._outbound if not params.get("state") or item["state"] == params["state"]]
                return {"requests": deepcopy(items[-max(1, min(int(params.get("limit") or 50), 200)):])}
            if method == "get_device_details":
                return {"device": {"device_query": deepcopy(self._device), "battery": deepcopy(self._battery)}}
        if method not in {"send_directed_private_message", "send_channel_message", "set_tx_power", "send_zero_hop_advert"}:
            raise ValueError("Unsupported USB gateway method.")
        if not self.allow_transmit:
            return {"accepted": False, "message": "USB gateway is read-only; restart with --allow-transmit to enable radio writes."}
        if not self._connected or self._loop is None:
            return {"accepted": False, "message": "USB node is disconnected."}
        future = asyncio.run_coroutine_threadsafe(self._send(method, dict(params)), self._loop)
        try:
            return future.result(timeout=self.timeout_seconds + 2)
        except FutureTimeoutError:
            future.cancel()
            return {"accepted": False, "message": "USB command outcome is indeterminate; no automatic resend was performed."}

    async def _send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._command_lock:
            client = self._client
            if client is None or not self._connected:
                return {"accepted": False, "message": "USB node is disconnected."}
            if method == "send_zero_hop_advert":
                result = await client.commands.send_advert(flood=False)
                accepted = result is not None and result.type.name == "OK"
                return {
                    "accepted": accepted,
                    "message": "Zero-hop advert accepted by the local node; reception is not confirmed." if accepted else "Zero-hop advert was not confirmed; no automatic retry.",
                    "payload": {"advert_type": "zero_hop", "flood": False, "requested_at": utc_now_iso()},
                }
            if method == "set_tx_power":
                power = int(params.get("tx_power_dbm", -1))
                if not 0 <= power <= int(self._self_info.get("max_tx_power") or 0):
                    return {"accepted": False, "message": "TX power is outside the node's range."}
                result = await client.commands.set_tx_power(power)
                accepted = result is not None and result.type.name == "OK"
                if accepted:
                    with self._lock:
                        self._self_info["tx_power"] = power
                return {"accepted": accepted, "message": "TX power updated." if accepted else "TX power rejected."}
            text = str(params.get("text") or "")
            plaintext = prepare_ham_plaintext(network="meshcore", text=text)
            if not plaintext.accepted or not plaintext.rendered_text or len(plaintext.rendered_text.encode("utf-8")) > 133:
                return {"accepted": False, "message": "Message must be nonempty ASCII plaintext, at most 133 bytes."}
            text = plaintext.rendered_text
            is_group = method == "send_channel_message"
            target = int(params.get("channel_index", -1)) if is_group else str(params.get("recipient_public_key") or "").lower()
            with self._lock:
                if is_group:
                    channel = next((channel for channel in self._channels if channel["index"] == target and not channel["is_empty"]), None)
                    if channel is None:
                        return {"accepted": False, "message": "Channel is not configured on this node."}
                    destination = {"channel_index": target, "channel_name": channel["name"]}
                else:
                    if target not in self._contacts or len([key for key in self._contacts if key.startswith(target[:12])]) != 1:
                        return {"accepted": False, "message": "Recipient must be a known, unambiguous node contact."}
                    destination = {"public_key": target, "native_address": target, "channel_index": -1}
                message_class = "channel" if is_group else "directed_private"
                delivery_id = str(params.get("delivery_id") or "").strip() or f"usb-{uuid4().hex}"
                previous = next((item for item in self._outbound if item["request_id"] == delivery_id), None)
                if previous is not None:
                    if previous["destination"] != destination or previous["payload"]["text"] != text or previous["message_class"] != message_class:
                        return {"accepted": False, "message": "Delivery ID conflicts with an earlier request."}
                    return {"accepted": previous["state"] in {"accepted_by_local_gateway", "acknowledged"}, "message": "Existing request status returned; no retransmission.", "payload": deepcopy(previous)}
                item = {
                    "request_id": delivery_id, "message_id": delivery_id, "message_class": message_class,
                    "state": "indeterminate", "destination": destination, "network": "meshcore", "accepted": False,
                    "payload": {"text": text, "channel_index": target if is_group else -1, "recipient_public_key": None if is_group else target},
                    "created_at": utc_now_iso(), "updated_at": utc_now_iso(),
                }
                self._outbound.append(item)
            command_started = time.monotonic()
            result = await (client.commands.send_chan_msg(target, text) if is_group else client.commands.send_msg(target, text))
            accepted = result is not None and result.type.name in {"OK", "MSG_SENT"}
            uncertain = result is None or (result.type.name == "ERROR" and (result.payload.get("reason") in {"timeout", "no_event_received"} or "error" in result.payload))
            with self._lock:
                item["state"] = "accepted_by_local_gateway" if accepted else "indeterminate" if uncertain else "rejected"
                item["accepted"] = accepted
                if accepted and not is_group:
                    code = result.payload.get("expected_ack")
                    item["expected_ack"] = code.hex() if isinstance(code, bytes) else str(code or "")
                    if item["expected_ack"] and any(code == item["expected_ack"] and observed >= command_started for code, observed in self._early_acks):
                        item["state"] = "acknowledged"
                item["updated_at"] = utc_now_iso()
                message = "Accepted by local USB node; RF receipt is not confirmed." if accepted else "USB command outcome is indeterminate; no automatic resend." if uncertain else "USB node rejected the message."
                return {"accepted": accepted, "message": message, "payload": deepcopy(item)}