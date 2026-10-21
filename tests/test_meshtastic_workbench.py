from __future__ import annotations

from collections import deque
from dataclasses import replace
from _thread import RLock
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.app.workbench_server import (
    build_meshtastic_gateway_payload,
    list_meshtastic_recipients_payload,
    poll_meshtastic_inbound_events,
    send_meshtastic_direct_message_payload,
    send_meshtastic_group_message_payload,
    _is_group_forward_echo,
    _remember_group_forward,
)
from ehn_basestation.core.contracts import DeliveryResult
from ehn_basestation.core.events import MessageEventStore
from ehn_basestation.gateways.meshtastic import MeshtasticGatewayAdapter
from ehn_basestation.gateways.registry import GatewayRegistry


class _FakeBroker:
    gateway_id = "meshtastic-office"

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.inbound: list[dict[str, object]] = []

    def get_status(self) -> dict[str, object]:
        return {
            "gateway_id": self.gateway_id,
            "connected": True,
            "status": "connected",
            "serial_port": "COM8",
            "node_id": "!70117298",
            "group_channel_index": 1,
        }

    def get_details(self) -> dict[str, object]:
        return {
            "runtime": {"transport": "meshtastic-serial"},
            "device": {"firmware_version": "test"},
            "channels": [],
            "observed_nodes": [],
            "observed_node_count": 0,
            "logs": [],
        }

    def list_events(self, *, limit: int = 50) -> list[dict[str, object]]:
        return [{"event_type": "text_rx", "text": "reply"}]

    def drain_inbound_events(self, *, limit: int = 50) -> list[dict[str, object]]:
        events = self.inbound[:limit]
        self.inbound = self.inbound[limit:]
        return events

    def send_text(self, *, text: str, destination_id: str, channel_index: int, want_ack: bool) -> dict[str, object]:
        result = {
            "accepted": True,
            "status": "accepted_by_local_gateway",
            "detail": "accepted",
            "transport_message_id": "123",
        }
        self.sent.append({
            "text": text,
            "destination_id": destination_id,
            "channel_index": channel_index,
            "want_ack": want_ack,
        })
        return result


class _FakeDirectoryStore:
    def __init__(self, entries) -> None:
        self.entries = list(entries)

    def list_users(self):
        return self.entries

    def get_user_by_id(self, user_id: str):
        return next((entry for entry in self.entries if entry.user.user_id == user_id), None)

    def find_user_by_endpoint(self, *, network: str, native_address: str):
        return next((
            entry for entry in self.entries
            if any(
                endpoint.network.upper() == network.upper()
                and endpoint.native_address == native_address
                for endpoint in entry.endpoints
            )
        ), None)


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.calls = []

    def send_directed_private(self, *, network, request):
        self.calls.append((network, request))
        return DeliveryResult(
            delivery_id=request.delivery_id,
            gateway_id="meshtastic-office",
            network=network,
            accepted=True,
            status="accepted_by_local_gateway",
            detail="Accepted by local gateway.",
            transport_message_id="123",
        )


class MeshtasticWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        settings = BasestationSettings.from_env(
            project_root=Path(self.temp_dir.name),
            env={
                "BASESTATION_MESHCORE_ENABLED": "0",
                "BASESTATION_MESHTASTIC_ENABLED": "1",
                "BASESTATION_MESHTASTIC_GATEWAY_ID": "meshtastic-office",
                "BASESTATION_MESHTASTIC_GROUP_CHANNEL_INDEX": "1",
                "BASESTATION_SMS_ENABLED": "0",
            },
        )
        self.broker = _FakeBroker()
        registry = GatewayRegistry()
        registry.register(MeshtasticGatewayAdapter(broker=self.broker))
        endpoint = SimpleNamespace(
            endpoint_id="endpoint-meshtastic-jane",
            network="MESHTASTIC",
            native_address="!a1ad6a0c",
            enabled=True,
            priority=100,
            verified=True,
        )
        self.entry = SimpleNamespace(
            user=SimpleNamespace(user_id="jane", display_name="Jane Example", status="active"),
            identity=SimpleNamespace(display_name="jane-node", identity_type="USER", messaging_mode="BOTH"),
            endpoints=(endpoint,),
        )
        self.self_entry = SimpleNamespace(
            user=SimpleNamespace(user_id="local-node", display_name="Local Node", status="active"),
            identity=SimpleNamespace(display_name="office-node", identity_type="STATION", messaging_mode="BOTH"),
            endpoints=(SimpleNamespace(
                endpoint_id="endpoint-meshtastic-self",
                network="MESHTASTIC",
                native_address="!70117298",
                enabled=True,
                priority=100,
                verified=True,
            ),),
        )
        self.group_entry = SimpleNamespace(
            user=SimpleNamespace(user_id="group-only", display_name="Group Only", status="active"),
            identity=SimpleNamespace(display_name="group-node", identity_type="USER", messaging_mode="GROUP"),
            endpoints=(SimpleNamespace(
                endpoint_id="endpoint-meshtastic-group",
                network="MESHTASTIC",
                native_address="!bbccdd11",
                enabled=True,
                priority=90,
                verified=True,
            ),),
        )
        self.orchestrator = _FakeOrchestrator()
        self.app = SimpleNamespace(
            settings=settings,
            gateway_registry=registry,
            directory_store=_FakeDirectoryStore([self.entry, self.self_entry, self.group_entry]),
            authority_directory_store=_FakeDirectoryStore([]),
            delivery_orchestrator=self.orchestrator,
            meshtastic_broker=self.broker,
            inbound_router=SimpleNamespace(enrich_observed_sender_display_name=Mock()),
            group_forward_echoes=deque(maxlen=200),
            group_echo_lock=RLock(),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_gateway_payload_exposes_status_and_recent_events(self) -> None:
        payload = build_meshtastic_gateway_payload(self.app)

        self.assertTrue(payload["available"])
        self.assertEqual(payload["gateway"]["serial_port"], "COM8")
        self.assertEqual(payload["events"][0]["text"], "reply")
        self.assertEqual(payload["details"]["device"]["firmware_version"], "test")

    def test_gateway_refresh_enriches_observed_sender_from_cached_node_name(self) -> None:
        details = self.broker.get_details()
        details["observed_nodes"] = [
            {"node_id": "!a1ad6a0c", "long_name": "mary jones", "short_name": "mary"},
            {"node_id": "!bbccdd11", "short_name": "short"},
            {"node_id": "!11223344"},
        ]
        with patch.object(self.broker, "get_details", return_value=details):
            payload = build_meshtastic_gateway_payload(self.app)

        enrich = self.app.inbound_router.enrich_observed_sender_display_name
        self.assertEqual(enrich.call_count, 2)
        enrich.assert_any_call(network="meshtastic", endpoint="!a1ad6a0c", display_name="mary jones")
        enrich.assert_any_call(network="meshtastic", endpoint="!bbccdd11", display_name="short")
        self.assertEqual(payload["details"], details)
        self.assertEqual(self.broker.sent, [])

    def test_group_send_uses_only_configured_channel(self) -> None:
        from http import HTTPStatus

        status, payload = send_meshtastic_group_message_payload(self.app, {"text": "& status check"})

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["accepted"])
        self.assertEqual(self.broker.sent[0]["destination_id"], "^all")
        self.assertEqual(self.broker.sent[0]["channel_index"], 1)
        self.assertFalse(self.broker.sent[0]["want_ack"])

    def test_group_send_rejects_other_channels_and_non_ascii(self) -> None:
        from http import HTTPStatus

        wrong_channel = send_meshtastic_group_message_payload(
            self.app,
            {"channel_index": 0, "text": "not the group channel"},
        )
        non_ascii = send_meshtastic_group_message_payload(self.app, {"text": "café"})

        self.assertEqual(wrong_channel[0], HTTPStatus.BAD_REQUEST)
        self.assertEqual(non_ascii[0], HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.broker.sent, [])

    def test_group_echo_guard_is_bounded_channel_specific_and_expires(self) -> None:
        with patch("ehn_basestation.app.workbench_server.time.monotonic", return_value=1000):
            for index in range(250):
                _remember_group_forward(self.app, network="meshtastic", channel_index=1, text=f"message {index}")
        self.assertEqual(len(self.app.group_forward_echoes), 200)
        with patch("ehn_basestation.app.workbench_server.time.monotonic", return_value=1300):
            self.assertTrue(_is_group_forward_echo(self.app, network="meshtastic", channel_index=1, text="message 249"))
            self.assertFalse(_is_group_forward_echo(self.app, network="meshtastic", channel_index=2, text="message 249"))
            self.assertFalse(_is_group_forward_echo(self.app, network="meshcore", channel_index=1, text="message 249"))
        with patch("ehn_basestation.app.workbench_server.time.monotonic", return_value=1301):
            self.assertFalse(_is_group_forward_echo(self.app, network="meshtastic", channel_index=1, text="message 249"))

    def test_direct_recipients_come_from_enabled_directory_endpoints(self) -> None:
        payload = list_meshtastic_recipients_payload(self.app)

        self.assertEqual(len(payload["recipients"]), 1)
        self.assertEqual(payload["recipients"][0]["node_id"], "!a1ad6a0c")
        self.assertEqual(payload["recipients"][0]["identity_label"], "jane-node")
        self.assertEqual(payload["recipients"][0]["identity_type"], "USER")
        self.assertEqual(payload["recipients"][0]["display_name"], "Jane Example")
        self.assertEqual({peer["user_id"] for peer in payload["directory_peers"]}, {"jane", "group-only"})

    def test_locally_enabled_observed_user_is_directly_reachable(self) -> None:
        from http import HTTPStatus

        self.entry.user.status = "observed"
        self.entry.user.retention_class = "FIELD_OBSERVED"
        self.entry.identity.display_name = self.entry.user.user_id
        self.entry.endpoints[0].verified = False
        self.entry.endpoints[0].enabled = True

        recipients = list_meshtastic_recipients_payload(self.app)
        status, payload = send_meshtastic_direct_message_payload(
            self.app,
            {"target_user_id": "jane", "text": "@jane local enable check"},
        )

        self.assertEqual([peer["user_id"] for peer in recipients["recipients"]], ["jane"])
        self.assertEqual(recipients["recipients"][0]["identity_label"], "Observed")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["accepted"])

        self.entry.identity.display_name = "mary-node"
        recipients = list_meshtastic_recipients_payload(self.app)
        self.assertEqual(recipients["recipients"][0]["identity_label"], "mary-node")

        self.entry.endpoints[0].enabled = False
        recipients = list_meshtastic_recipients_payload(self.app)
        status, _ = send_meshtastic_direct_message_payload(
            self.app,
            {"target_user_id": "jane", "text": "@jane disabled check"},
        )
        self.assertNotIn("jane", {peer["user_id"] for peer in recipients["recipients"]})
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(len(self.orchestrator.calls), 1)

    def test_direct_send_rejects_local_gateway_node(self) -> None:
        from http import HTTPStatus

        status, payload = send_meshtastic_direct_message_payload(
            self.app,
            {"target_user_id": "local-node", "text": "@office-node hello"},
        )

        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertIn("own node", payload["error"])
        self.assertEqual(self.orchestrator.calls, [])

    def test_direct_send_uses_delivery_orchestrator(self) -> None:
        from http import HTTPStatus

        status, payload = send_meshtastic_direct_message_payload(
            self.app,
            {"target_user_id": "jane", "text": "@jane check in"},
        )

        self.assertEqual(status, HTTPStatus.OK)
        self.assertTrue(payload["accepted"])
        self.assertEqual(len(self.orchestrator.calls), 1)
        network, request = self.orchestrator.calls[0]
        self.assertEqual(network, "meshtastic")
        self.assertEqual(request.destination["native_address"], "!a1ad6a0c")

    def test_inbound_group_message_is_sent_to_existing_group_fanout(self) -> None:
        class _Router:
            def _normalize_gateway_event(self, gateway_id, event, *, network):
                return SimpleNamespace(gateway_id=gateway_id, event=event, network=network)

            def ingest_inbound_text_message(self, normalized):
                self.normalized = normalized
                return SimpleNamespace(classification=SimpleNamespace(conversation_scope="group", action="route"))

        self.app.inbound_router = _Router()
        self.broker.inbound.append({
            "event_id": "4",
            "event_type": "text_rx",
            "message_class": "channel_message",
            "channel_index": 1,
            "sender_endpoint": "!a1ad6a0c",
            "text": "& status check",
        })

        with patch("ehn_basestation.app.workbench_server._queue_group_sms", return_value=1) as fanout:
            poll_meshtastic_inbound_events(self.app)

        fanout.assert_called_once_with(
            self.app,
            body="Meshtastic group from jane-node: & status check",
            event_key="meshtastic-group:4",
            exclude_user_id="jane",
        )

    def test_meshtastic_group_ingress_forwards_labeled_copy_to_meshcore_once(self) -> None:
        class _Router:
            def _normalize_gateway_event(self, gateway_id, event, *, network):
                return SimpleNamespace(gateway_id=gateway_id, event=event, network=network)

            def ingest_inbound_text_message(self, normalized):
                return SimpleNamespace(classification=SimpleNamespace(conversation_scope="group", action="route"))

        class _MeshCoreClient:
            def __init__(self) -> None:
                self.sent: list[dict[str, object]] = []

            def list_channels(self):
                return [
                    {"index": 0, "is_empty": False},
                    {"index": 1, "name": "private-test", "is_empty": False},
                ]

            def send_channel_message(self, **kwargs):
                self.sent.append(kwargs)
                return True, "accepted", {"message_id": "meshcore-group-1"}

        self.app.settings = replace(self.app.settings, meshcore_enabled=True)
        self.app.inbound_router = _Router()
        self.app.message_events = MessageEventStore()
        meshcore_client = _MeshCoreClient()
        self.broker.inbound.append({
            "event_id": "meshtastic-group-to-meshcore-1",
            "event_type": "text_rx",
            "message_class": "channel_message",
            "channel_index": 1,
            "sender_endpoint": "!a1ad6a0c",
            "sender_display_name": "Mary radio",
            "text": "& bridge check",
        })

        with patch("ehn_basestation.app.workbench_server.MeshCoreRuntimeClient.from_settings", return_value=meshcore_client):
            poll_meshtastic_inbound_events(self.app)
            poll_meshtastic_inbound_events(self.app)

        self.assertEqual(len(meshcore_client.sent), 1)
        self.assertEqual(meshcore_client.sent[0]["channel_index"], 1)
        self.assertEqual(meshcore_client.sent[0]["channel_name"], "private-test")
        self.assertEqual(meshcore_client.sent[0]["text"], "Meshtastic group from jane-node: & bridge check")
        self.assertTrue(any(
            entry[0] == "meshcore" and entry[1] == 1
            and entry[2] == "Meshtastic group from jane-node: & bridge check"
            for entry in self.app.group_forward_echoes
        ))


if __name__ == "__main__":
    unittest.main()