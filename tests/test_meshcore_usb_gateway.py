from __future__ import annotations

import threading
import time
from types import SimpleNamespace
import json
import socket
from unittest.mock import patch
import unittest

from ehn_basestation.gateways.meshcore.gateway_link import (
    GatewayLinkEnvelope, MeshCoreRuntimeSettings, StreamMeshCoreGatewayLink,
    decode_gateway_link_frame, encode_gateway_link_frame,
)
from ehn_basestation.gateways.meshcore.usb_gateway import MeshCoreUsbGatewayServer
from ehn_basestation.gateways.meshcore.usb_broker import MeshCoreUsbBroker


def _event(name, payload=None):
    return SimpleNamespace(type=SimpleNamespace(name=name), payload=payload or {})


class _FakeSdk:
    def __init__(self):
        self.self_info = {"name": "Fake USB", "public_key": "ff" * 32, "max_tx_power": 22}
        self.commands = self
        self.callback = None
        self.calls = []
        self.closed = False
        self.contacts = {"ab" * 32: {"adv_name": "Peer"}}
        self.contact_queries = 0

    def subscribe(self, kind, callback):
        self.callback = callback

    async def disconnect(self):
        self.closed = True

    async def send_device_query(self):
        return _event("DEVICE_INFO", {"max_channels": 2, "model": "Fake", "ver": "test"})

    async def get_bat(self):
        return _event("BATTERY", {"level": 4100})

    async def get_contacts(self, timeout=5):
        self.contact_queries += 1
        return _event("CONTACTS", self.contacts)

    async def get_channel(self, index):
        return _event("CHANNEL_INFO", {"channel_idx": index, "channel_name": "test", "channel_secret": b"private-secret!!"})

    async def get_msg(self, timeout=None):
        return _event("NO_MORE_MSGS")

    async def send_msg(self, target, text):
        self.calls.append(("direct", target, text))
        self.callback(_event("ACK", {"code": "01020304"}))
        return _event("MSG_SENT", {"expected_ack": b"\x01\x02\x03\x04"})

    async def send_chan_msg(self, target, text):
        self.calls.append(("group", target, text))
        return _event("OK")

    async def send_advert(self, flood=False):
        self.calls.append(("advert", flood))
        return _event("OK")


class _FakeBroker:
    def dispatch(self, method, params):
        if method == "get_snapshot":
            return {"snapshot": {"connected": True, "status": "connected", "identity": {"display_name": "USB node"}}}
        if method == "list_channels":
            return {"channels": [{"index": 1, "name": "test"}]}
        raise ValueError("Unsupported method.")


class MeshCoreUsbGatewayTests(unittest.TestCase):
    def setUp(self):
        self.sdk = _FakeSdk()

        async def factory():
            return self.sdk

        self.broker = MeshCoreUsbBroker(serial_port="fake", sdk_factory=factory)
        self.broker.start()
        self.assertTrue(self.broker.wait_until_ready(3))

    def tearDown(self):
        self.broker.stop()
        self.assertTrue(self.sdk.closed)

    def test_readonly_broker_does_not_transmit(self):
        result = self.broker.dispatch("send_channel_message", {"channel_index": 1, "text": "hello"})
        self.assertFalse(result["accepted"])
        self.assertEqual(self.sdk.calls, [])
        self.assertEqual(self.broker.snapshot()["battery"]["source"], "external_node")
        self.assertNotIn("private-secret", json.dumps(self.broker.dispatch("list_channels", {})))

    def test_direct_send_ack_and_duplicate_id(self):
        self.broker.allow_transmit = True
        params = {"recipient_public_key": "ab" * 32, "text": "hello", "delivery_id": "delivery-1"}
        first = self.broker.dispatch("send_directed_private_message", params)
        self.assertTrue(first["accepted"])
        self.assertEqual(first["payload"]["state"], "acknowledged")
        self.assertTrue(self.broker.dispatch("send_directed_private_message", params)["accepted"])
        self.assertFalse(self.broker.dispatch("send_directed_private_message", {**params, "text": "changed"})["accepted"])
        self.assertEqual(len(self.sdk.calls), 1)
        json.dumps(first)

    def test_send_validation_and_group_local_acceptance(self):
        self.broker.allow_transmit = True
        for text in ("", "caf\u00e9", "x" * 134):
            self.assertFalse(self.broker.dispatch("send_channel_message", {"channel_index": 1, "text": text})["accepted"])
        self.assertFalse(self.broker.dispatch("send_channel_message", {"channel_index": 99, "text": "hello"})["accepted"])
        self.assertFalse(self.broker.dispatch("send_directed_private_message", {"recipient_public_key": "cd" * 32, "text": "hello"})["accepted"])
        result = self.broker.dispatch("send_channel_message", {"channel_index": 1, "text": "hello"})
        self.assertTrue(result["accepted"])
        self.assertEqual(result["payload"]["state"], "accepted_by_local_gateway")
        self.assertEqual(result["payload"]["destination"], {"channel_index": 1, "channel_name": "test"})

    def test_inbound_sender_resolution_dedupe_and_bounds(self):
        self.broker._contacts["ab" * 32] = {"adv_name": "N@DH-TBEAM"}
        incoming = _event("CONTACT_MSG_RECV", {"pubkey_prefix": "ab" * 6, "sender_timestamp": 1, "txt_type": 0, "text": "hello"})
        self.sdk.callback(incoming)
        self.sdk.callback(incoming)
        events = self.broker.dispatch("list_gateway_inbound_events", {})["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["sender"]["public_key"], "ab" * 32)
        self.assertEqual(events[0]["payload"]["sender_display_name"], "N@DH-TBEAM")
        for index in range(300):
            self.sdk.callback(_event("CHANNEL_MSG_RECV", {"channel_idx": 1, "sender_timestamp": index, "text": f"message {index}"}))
        self.assertEqual(len(self.broker.dispatch("list_gateway_inbound_events", {"limit": 999})["events"]), 250)

    def test_advert_refresh_enriches_previously_unresolved_inbound_event(self):
        prefix = "cd" * 6
        incoming = _event("CONTACT_MSG_RECV", {"pubkey_prefix": prefix, "sender_timestamp": 1, "txt_type": 0, "text": "@burner hello"})
        self.sdk.callback(incoming)
        before = self.broker.dispatch("list_gateway_inbound_events", {})["events"][0]
        self.assertIsNone(before["payload"]["sender"]["public_key"])
        self.sdk.contacts["cd" * 6 + "ef" * 26] = {"adv_name": "N2DH-TBEAM"}
        self.sdk.callback(_event("ADVERTISEMENT", {"public_key": "cd" * 6 + "ef" * 26}))
        deadline = time.monotonic() + 2
        enriched = before
        while time.monotonic() < deadline:
            enriched = self.broker.dispatch("list_gateway_inbound_events", {})["events"][0]
            if enriched["payload"]["sender"].get("display_name"):
                break
            time.sleep(0.02)
        self.assertEqual(enriched["payload"]["sender"]["public_key"], "cd" * 6 + "ef" * 26)
        self.assertEqual(enriched["payload"]["sender_display_name"], "N2DH-TBEAM")

    def test_disconnected_broker_rejects_radio_work(self):
        self.broker.allow_transmit = True
        self.sdk.callback(_event("DISCONNECTED"))
        self.assertFalse(self.broker.snapshot()["connected"])
        self.assertFalse(self.broker.wait_until_ready(0))
        self.assertFalse(self.broker.dispatch("send_channel_message", {"channel_index": 1, "text": "hello"})["accepted"])

    def test_zero_hop_advert_is_transmit_gated_and_never_floods(self):
        self.assertFalse(self.broker.dispatch("send_zero_hop_advert", {"flood": True})["accepted"])
        self.assertEqual(self.sdk.calls, [])
        self.broker.allow_transmit = True
        result = self.broker.dispatch("send_zero_hop_advert", {"flood": True})
        self.assertTrue(result["accepted"])
        self.assertFalse(result["payload"]["flood"])
        self.assertEqual(self.sdk.calls, [("advert", False)])

    def test_broker_reconnects_without_retransmitting(self):
        self.sdk.callback(_event("DISCONNECTED"))
        self.assertTrue(self.broker.wait_until_ready(5))
        self.assertTrue(self.broker.snapshot()["connected"])
        self.assertEqual(self.sdk.calls, [])

    def test_uncertain_send_is_not_repeated_for_the_same_delivery_id(self):
        self.broker.allow_transmit = True
        calls = []

        async def uncertain(target, text):
            calls.append((target, text))
            return _event("ERROR", {"reason": "no_event_received"})

        params = {"channel_index": 1, "text": "hello", "delivery_id": "uncertain-1"}
        with patch.object(self.sdk, "send_chan_msg", side_effect=uncertain):
            result = self.broker.dispatch("send_channel_message", params)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["payload"]["state"], "indeterminate")
            self.assertFalse(self.broker.dispatch("send_channel_message", params)["accepted"])
        self.assertEqual(len(calls), 1)

    def test_ambiguous_sender_prefix_does_not_invent_a_full_identity(self):
        self.broker._contacts["ab" * 6 + "cd" * 26] = {"adv_name": "Collision"}
        self.sdk.callback(_event("CONTACT_MSG_RECV", {"pubkey_prefix": "ab" * 6, "sender_timestamp": 1, "text": "hello"}))
        event = self.broker.dispatch("list_gateway_inbound_events", {})["events"][0]
        self.assertIsNone(event["payload"]["sender"]["public_key"])
        self.broker.allow_transmit = True
        self.assertFalse(self.broker.dispatch("send_directed_private_message", {"recipient_public_key": "ab" * 32, "text": "hello"})["accepted"])

    def test_gateway_rejects_invalid_envelopes_and_oversized_frames(self):
        with MeshCoreUsbGatewayServer(("127.0.0.1", 0), self.broker) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                frames = [
                    encode_gateway_link_frame(GatewayLinkEnvelope(version="wrong", type="request", id="bad", method="get_snapshot")),
                    (1_048_577).to_bytes(4, "big"),
                ]
                for frame in frames:
                    with socket.create_connection(server.server_address, timeout=2) as connection:
                        connection.sendall(frame)
                        with connection.makefile("rb") as reader:
                            header = reader.read(4)
                            payload = reader.read(int.from_bytes(header, "big"))
                        self.assertFalse(decode_gateway_link_frame(header + payload).ok)
            finally:
                server.shutdown()
                worker.join(timeout=2)

    def test_existing_basestation_client_reads_standalone_gateway(self):
        with MeshCoreUsbGatewayServer(("127.0.0.1", 0), _FakeBroker()) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                link = StreamMeshCoreGatewayLink(
                    MeshCoreRuntimeSettings("usb-test", "USB test", "", "1.0"),
                    carrier="tcp", endpoint=f"127.0.0.1:{server.server_address[1]}",
                )
                self.assertTrue(link.get_snapshot()["connected"])
                self.assertEqual(link.list_channels()[0]["index"], 1)
                self.assertFalse(link.send_channel_message(channel_index=1, text="no RF")[0])
            finally:
                server.shutdown()
                worker.join(timeout=2)

    def test_server_rejects_nonlocal_bind(self):
        with self.assertRaises(ValueError):
            MeshCoreUsbGatewayServer(("0.0.0.0", 0), _FakeBroker())


if __name__ == "__main__":
    unittest.main()