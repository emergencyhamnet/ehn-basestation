from __future__ import annotations

from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
import json
from unittest.mock import Mock, patch

from ehn_basestation.gateways.meshtastic.broker import MeshtasticGatewayBroker


class _FakePublisher:
    def __init__(self) -> None:
        self.listeners: dict[str, list[object]] = {}

    def subscribe(self, listener, topic: str) -> None:
        self.listeners.setdefault(topic, []).append(listener)

    def unsubscribe(self, listener, topic: str) -> None:
        self.listeners[topic].remove(listener)

    def publish(self, topic: str, **kwargs) -> None:
        for subscribed_topic, listeners in list(self.listeners.items()):
            if topic == subscribed_topic or topic.startswith(f"{subscribed_topic}."):
                for listener in list(listeners):
                    listener(**kwargs)


class _FakeNode:
    nodeNum = 0x1234ABCD
    channels = []

    def getLongName(self) -> str:
        return "EHN Test Node"

    def getShortName(self) -> str:
        return "EHN2"


class _FakeInterface:
    def __init__(self) -> None:
        self.isConnected = Event()
        self.isConnected.set()
        self.localNode = _FakeNode()
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.nodes: dict[str, dict[str, object]] = {}
        self.metadata = None

    def sendText(self, text: str, **kwargs):
        self.sent.append({"text": text, **kwargs})
        return SimpleNamespace(id=456)

    def close(self) -> None:
        self.closed = True
        self.isConnected.clear()


class MeshtasticGatewayBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.publisher = _FakePublisher()
        self.interface = _FakeInterface()
        self.factory_calls = 0

        def make_interface(port: str) -> _FakeInterface:
            self.factory_calls += 1
            self.assertEqual(port, "COM8")
            return self.interface

        self.broker = MeshtasticGatewayBroker(
            gateway_id="meshtastic-office",
            display_name="Office Meshtastic",
            serial_port="COM8",
            group_channel_index=1,
            interface_factory=make_interface,
            publisher=self.publisher,
            reconnect_seconds=0.01,
        )
        self.broker.start()
        self._wait_until_connected()

    def tearDown(self) -> None:
        self.broker.stop()

    def test_start_owns_one_interface_and_exposes_identity(self) -> None:
        self.broker.start()

        status = self.broker.get_status()

        self.assertEqual(self.factory_calls, 1)
        self.assertTrue(status["connected"])
        self.assertEqual(status["node_id"], "!1234abcd")
        self.assertEqual(status["node_name"], "EHN Test Node")
        self.assertEqual(status["group_channel_index"], 1)

    def test_default_interface_loads_stored_node_directory(self) -> None:
        serial_interface = Mock(return_value=self.interface)
        self.broker._interface_factory = None
        with patch.dict("sys.modules", {
            "meshtastic.serial_interface": SimpleNamespace(SerialInterface=serial_interface),
        }):
            self.assertIs(self.broker._create_interface(), self.interface)
        serial_interface.assert_called_once_with(devPath="COM8", timeout=20, noNodes=False)

    def test_send_text_returns_local_acceptance_and_passes_ack_option(self) -> None:
        result = self.broker.send_text(text="ping", want_ack=True)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["status"], "accepted_by_local_gateway")
        self.assertEqual(result["transport_message_id"], "456")
        self.assertEqual(self.interface.sent[0]["destinationId"], "^all")
        self.assertEqual(self.interface.sent[0]["channelIndex"], 1)
        self.assertTrue(self.interface.sent[0]["wantAck"])
        self.assertEqual(self.interface.sent[0]["onResponse"].__name__, "onAckNak")
        self.assertNotIn("onResponseAckPermitted", self.interface.sent[0])

    def test_ack_updates_history_without_claiming_remote_reply(self) -> None:
        self.broker.send_text(text="ping", want_ack=True)
        callback = self.interface.sent[0]["onResponse"]

        callback({"decoded": {"routing": {"errorReason": "NONE"}}})

        statuses = [event["status"] for event in self.broker.list_events()]
        self.assertEqual(statuses, ["accepted_by_local_gateway", "acknowledged"])

    def test_nak_is_not_recorded_as_acknowledged(self) -> None:
        self.broker.send_text(text="ping", want_ack=True)
        callback = self.interface.sent[0]["onResponse"]

        callback({"decoded": {"routing": {"errorReason": "NO_ROUTE"}}})

        statuses = [event["status"] for event in self.broker.list_events()]
        self.assertEqual(statuses[-1], "nak")

    def test_ack_event_history_is_json_serializable(self) -> None:
        self.broker.send_text(text="ping", want_ack=True)
        callback = self.interface.sent[0]["onResponse"]

        callback({
            "id": 77,
            "fromId": "!a1ad6a0c",
            "decoded": {
                "requestId": 456,
                "routing": {"errorReason": "NONE"},
                "payload": b"protobuf bytes",
            },
        })

        encoded = json.dumps(self.broker.list_events())
        self.assertIn('"status": "acknowledged"', encoded)
        self.assertNotIn("protobuf bytes", encoded)

    def test_receive_routes_channel_one_group_and_direct_messages(self) -> None:
        self.publisher.publish(
            "meshtastic.receive.text",
            packet={
                "id": 10,
                "fromId": "!a1ad6a0c",
                "toId": "^all",
                "to": 0xFFFFFFFF,
                "channel": 1,
                "user": {"longName": "EHN-2"},
                "decoded": {"text": "& status check"},
            },
            interface=self.interface,
        )
        self.publisher.publish(
            "meshtastic.receive.text",
            packet={
                "id": 11,
                "fromId": "!a1ad6a0c",
                "toId": "^all",
                "to": 0xFFFFFFFF,
                "channel": 0,
                "decoded": {"text": "not the configured group channel"},
            },
            interface=self.interface,
        )
        self.publisher.publish(
            "meshtastic.receive.text",
            packet={
                "id": 12,
                "fromId": "!a1ad6a0c",
                "toId": "!1234abcd",
                "to": 0x1234ABCD,
                "channel": 0,
                "decoded": {"text": "direct reply"},
            },
            interface=self.interface,
        )
        self.publisher.publish(
            "meshtastic.receive.position",
            packet={
                "id": 13,
                "fromId": "!a1ad6a0c",
                "toId": "^all",
                "to": 0xFFFFFFFF,
                "channel": 1,
                "decoded": {"portnum": "POSITION_APP"},
            },
            interface=self.interface,
        )

        inbound = self.broker.drain_inbound_events()
        activity = self.broker.list_events()

        self.assertEqual([event["text"] for event in inbound], ["& status check", "direct reply"])
        self.assertEqual(inbound[0]["source_endpoint"], "!a1ad6a0c")
        self.assertEqual(inbound[0]["message_class"], "channel_message")
        self.assertEqual(inbound[1]["message_class"], "direct_message")
        self.assertTrue(any(event["event_type"] == "packet_rx" and event["message_class"] == "position_app" for event in activity))

    def test_text_sender_name_uses_cached_node_when_packet_has_no_user(self) -> None:
        self.interface.nodes = {
            "!a1ad6a0c": {
                "user": {"id": "!a1ad6a0c", "longName": "mary jones", "shortName": "mary"},
            }
        }
        self.publisher.publish(
            "meshtastic.receive.text",
            packet={
                "id": 104,
                "from": 0xA1AD6A0C,
                "to": 0x1234ABCD,
                "decoded": {"text": "@burner name check"},
            },
            interface=self.interface,
        )

        event = self.broker.drain_inbound_events()[0]
        self.assertEqual(event["sender_endpoint"], "!a1ad6a0c")
        self.assertEqual(event["sender_display_name"], "mary jones")

    def test_text_retains_name_learned_from_previous_user_packet(self) -> None:
        for packet_id, user in ((105, {"shortName": "mary"}), (106, None)):
            packet = {
                "id": packet_id,
                "fromId": "!a1ad6a0c",
                "toId": "!1234abcd",
                "decoded": {"text": "@burner name check"},
            }
            if user is not None:
                packet["user"] = user
            self.publisher.publish("meshtastic.receive.text", packet=packet, interface=self.interface)

        self.assertEqual(
            [event["sender_display_name"] for event in self.broker.drain_inbound_events()],
            ["mary", "mary"],
        )
        self.assertEqual(self.broker.get_details()["observed_nodes"][0]["short_name"], "mary")

    def test_details_project_nodes_device_channels_and_log_tail(self) -> None:
        self.interface.nodes = {
            "!a1ad6a0c": {
                "num": 2715623948,
                "user": {"id": "!a1ad6a0c", "longName": "Lilygo", "shortName": "mary", "hwModel": "TBEAM"},
                "lastHeard": 1790870400,
                "snr": 7.5,
                "hopsAway": 1,
                "deviceMetrics": {"batteryLevel": 91, "channelUtilization": 2.5},
            }
        }
        self.interface.metadata = SimpleNamespace(
            firmware_version="2.7.15",
            hw_model=1,
            device_state_version=23,
            canShutdown=True,
            hasWifi=True,
            hasBluetooth=True,
            hasEthernet=False,
        )
        self.interface.localNode.channels = [
            SimpleNamespace(
                index=1,
                role=1,
                settings=SimpleNamespace(name="EHN Group", uplink_enabled=True, downlink_enabled=True, psk=b"secret"),
            )
        ]
        self.publisher.publish("meshtastic.log.line", line="\x1b[0mserial link ready\x1b[0m", interface=self.interface)

        details = self.broker.get_details()
        encoded = json.dumps(details)

        self.assertEqual(details["observed_nodes"][0]["node_id"], "!a1ad6a0c")
        self.assertEqual(details["observed_nodes"][0]["long_name"], "Lilygo")
        self.assertEqual(details["device"]["firmware_version"], "2.7.15")
        self.assertEqual(details["channels"][0]["name"], "EHN Group")
        self.assertEqual(details["logs"][0]["line"], "serial link ready")
        self.assertNotIn("secret", encoded)

    def test_unknown_numeric_sender_routes_private_channel_and_direct_text(self) -> None:
        for packet_id, destination, channel, text in (
            (101, 0xFFFFFFFF, 1, "private group reply"),
            (102, 0x1234ABCD, 0, "@steve direct reply"),
        ):
            self.publisher.publish(
                "meshtastic.receive.text",
                packet={
                    "id": packet_id,
                    "from": 0xA1AD6A0C,
                    "fromId": None,
                    "to": destination,
                    "toId": None,
                    "channel": channel,
                    "decoded": {"text": text, "portnum": "TEXT_MESSAGE_APP"},
                },
                interface=self.interface,
            )

        inbound = self.broker.drain_inbound_events()
        self.assertEqual(len(inbound), 2)
        self.assertEqual([event["source_endpoint"] for event in inbound], ["!a1ad6a0c"] * 2)
        self.assertEqual(inbound[0]["message_class"], "channel_message")
        self.assertEqual(inbound[1]["message_class"], "direct_message")

    def test_unknown_numeric_sender_packet_is_visible_in_all_activity(self) -> None:
        self.publisher.publish(
            "meshtastic.receive.position",
            packet={
                "id": 103,
                "from": 0xA1AD6A0C,
                "fromId": None,
                "decoded": {"portnum": "POSITION_APP"},
            },
            interface=self.interface,
        )
        self.assertEqual(self.broker.list_events()[-1]["sender_endpoint"], "!a1ad6a0c")
        self.assertEqual(self.broker.drain_inbound_events(), [])

    def _wait_until_connected(self) -> None:
        deadline = monotonic() + 2
        while monotonic() < deadline:
            if self.broker.get_status()["connected"]:
                return
            sleep(0.01)
        self.fail("Meshtastic broker did not connect to the fake interface")


if __name__ == "__main__":
    unittest.main()