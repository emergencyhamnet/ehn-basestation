from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.app.bootstrap import create_app
from ehn_basestation.app.workbench_server import (
    LocalProcessRegistry,
    WorkbenchConfig,
    build_android_sms_link_status_payload,
    build_basestation_processes_payload,
    build_directory_payload,
    build_contacts_payload,
    build_workbench_identity_payload,
    build_workbench_delivery_queue_payload,
    build_workbench_sinks_payload,
    control_basestation_process_payload,
    dispatch_meshcore_queued_messages_payload,
    dispatch_sink_queued_messages_payload,
    build_meshcore_gateway_payload,
    build_sms_gateway_payload,
    route_inbound_private_to_user_payload,
    claim_android_sms_outbound_payload,
    complete_android_sms_outbound_payload,
    build_device_payload,
    build_logs_payload,
    build_messages_payload,
    build_node_payload,
    receive_android_sms_inbound_payload,
    poll_meshcore_inbound_events,
    poll_meshtastic_inbound_events,
    _initialize_meshcore_group_ingest,
    _meshcore_group_sender_label,
    _select_target_delivery_endpoint,
    save_directory_user_payload,
    send_meshcore_private_channel_message_payload,
    send_meshcore_zero_hop_advert_payload,
    _normalize_meshcore_event_for_routing,
    send_sms_gateway_message_payload,
    build_runtime_payload,
    set_node_tx_power_payload,
    send_directed_private_message_payload,
)
from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest
from ehn_basestation.gateways.meshtastic import MeshtasticGatewayAdapter


class _FakeManagedProcess:
    def __init__(self, pid: int = 43210) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9


class _FakeMeshCoreRuntimeClient:
    def __init__(self) -> None:
        self.last_directed_private_message: dict[str, object] | None = None
        self.last_channel_message: dict[str, object] | None = None

    @property
    def gateway_id(self) -> str:
        return "meshcore-gateway-1"

    def get_snapshot(self):
        return {
            "connected": True,
            "status": "connected",
            "display_name": "Pi Probe",
            "contract_version": "1.0",
            "identity": {
                "display_name": "pi-port-probe",
                "lat": 51.5074,
                "lon": -0.1278,
                "ble_pin": 123456,
                "gps_interval_s": 300,
                "default_scope_name": "ehn-primary",
                "tx_power_max_dbm": 17,
            },
            "runtime_status": {
                "link_carrier": "tcp",
                "link_endpoint": "127.0.0.1:7463",
                "freq_mhz": 910.525,
                "bw_khz": 62.5,
                "sf": 7,
                "cr": 5,
                "tx_power_dbm": 15,
                "interface_mode": "gateway-link",
                "path_hash_mode": 1,
            },
            "last_error": None,
        }

    def list_channels(self):
        return [
            {"index": 0, "name": "public", "secret_preview": "system", "is_empty": False},
            {"index": 1, "name": "private-test", "secret_preview": "abcd1234...eeff7788", "secret_hex": "abcd", "is_empty": False},
        ]

    def list_observed_nodes(self):
        return [
            {
                "public_key": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                "contact_name": "probe-contact",
                "last_seen_at": "2026-08-23T21:30:00+00:00",
                "latitude": 51.5074,
                "longitude": -0.1278,
                "hops": 2,
            }
        ]

    def list_gateway_inbound_events(self, *, limit=50, cursor=None):
        return [
            {
                "event_type": "meshcore_private_rx",
                "event_ts_utc": "2026-08-23T21:31:00+00:00",
                "transport": "meshcore-runtime",
                "summary": "Good stuff",
                "payload": {
                    "text": "Good stuff",
                    "message_scope": "direct",
                    "sender": {
                        "public_key": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                        "short_name": "1234567890ab",
                    },
                    "raw": {"channel_index": -1, "message_scope": "direct"},
                },
            }
        ]

    def list_gateway_outbound_status(self, *, limit=50, state=None):
        return [
            {
                "message_class": "group",
                "state": "sent",
                "updated_at": "2026-08-23T21:32:00+00:00",
                "payload": {"text": "Roger that"},
                "destination": {"channel_index": 0},
            }
        ]

    def get_device_details(self):
        return {
            "device_query": {
                "firmware_version_code": 13,
                "max_contacts": 200,
                "max_channels": 8,
                "ble_pin": 123456,
                "firmware_build": "6 Jun 2026",
                "model": "Pi Probe Board",
                "version": "v1.16.0",
                "client_repeat": 1,
                "path_hash_mode": 1,
            },
            "battery": {
                "battery_mv": 4100,
                "used_kb": 128,
                "total_kb": 1024,
            },
            "stats": {
                "core": {"battery_mv": 4100, "uptime_secs": 3600, "errors": 0, "queue_len": 1},
                "radio": {"noise_floor": -116, "last_rssi": -78, "last_snr": 7.5, "tx_air_secs": 12, "rx_air_secs": 98},
                "packets": {"recv": 44, "sent": 21, "flood_tx": 12, "direct_tx": 9, "flood_rx": 31, "direct_rx": 13, "recv_errors": 1},
            },
        }

    def send_directed_private_message(self, **kwargs):
        self.last_directed_private_message = dict(kwargs)
        return True, "Directed private message accepted by gateway link.", {
            "request_id": "req-1",
            "recipient_public_key": kwargs["recipient_public_key"],
            "text": kwargs["text"],
        }

    def send_channel_message(self, **kwargs):
        self.last_channel_message = dict(kwargs)
        return True, "Channel message accepted by gateway link.", {
            "request_id": "req-channel-1",
            "channel_index": kwargs["channel_index"],
            "channel_name": kwargs.get("channel_name"),
            "text": kwargs["text"],
        }

    def set_tx_power(self, **kwargs):
        return True, f"TX power set to {kwargs['tx_power_dbm']} dBm.", {"tx_power_dbm": kwargs["tx_power_dbm"]}


class WorkbenchServerPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = BasestationSettings.from_env(
            env={
                "BASESTATION_MESHCORE_ENABLED": "1",
                "BASESTATION_SMS_ENABLED": "0",
                "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
                "BASESTATION_MESHCORE_LINK_ENDPOINT": "127.0.0.1:7463",
                "BASESTATION_MESHCORE_BASE_URL": "",
            }
        )
        self.client = _FakeMeshCoreRuntimeClient()
        self.last_delivery_request: DirectedPrivateDeliveryRequest | None = None

    def _capture_direct_delivery(self, *, network: str, request: DirectedPrivateDeliveryRequest) -> DeliveryResult:
        self.last_delivery_request = request
        return DeliveryResult(
            delivery_id=request.delivery_id,
            gateway_id="meshcore-gateway-1",
            network=network,
            accepted=True,
            status="sent",
            detail="Directed private message accepted by gateway link.",
            transport_message_id="req-directed-1",
        )

    def test_meshcore_endpoint_can_be_selected_without_direct_runtime_client(self) -> None:
        app = SimpleNamespace(
            delivery_store=SimpleNamespace(
                get_delivery_policy=lambda user_id: None,
                get_user_presence=lambda user_id: None,
            ),
            gateway_registry=SimpleNamespace(
                find_provider_for_network=lambda network: object() if network == "meshcore" else None,
            ),
        )
        target = SimpleNamespace(
            user=SimpleNamespace(user_id="user-tbeam"),
            endpoints=(SimpleNamespace(
                endpoint_id="endpoint-tbeam",
                network="MESHCORE",
                native_address="PUBKEY-TBEAM",
                enabled=True,
                verified=True,
                priority=100,
                trust_level="verified",
                capability_tags="direct",
            ),),
        )

        route, endpoint = _select_target_delivery_endpoint(app, target, meshcore_client=None)

        self.assertEqual(route, "meshcore")
        self.assertEqual(endpoint["native_address"], "PUBKEY-TBEAM")

    def test_runtime_payload_projects_gateway_link_status(self) -> None:
        payload = build_runtime_payload(self.settings, self.client)

        self.assertEqual(payload["interface_mode"], "gateway-link")
        self.assertEqual(payload["service"]["ActiveState"], "active")
        self.assertEqual(payload["status"]["parsed"]["link_carrier"], "tcp")

    def test_basestation_processes_payload_marks_workbench_observe_only(self) -> None:
        app = create_app(settings=self.settings)
        config = WorkbenchConfig(
            settings=self.settings,
            static_dir=Path("."),
            app=app,
            process_registry=LocalProcessRegistry(self.settings, workbench_host="127.0.0.1", workbench_port=18098),
            listen_host="127.0.0.1",
            listen_port=18098,
            started_at_utc="2026-09-07T00:00:00+00:00",
        )

        with patch("ehn_basestation.app.workbench_server._is_local_port_open", side_effect=lambda host, port: port == 8105):
            payload = build_basestation_processes_payload(config)

        processes = {item["id"]: item for item in payload["processes"]}
        self.assertEqual(processes["workbench-preview"]["status"], "running")
        self.assertFalse(processes["workbench-preview"]["control_available"])
        self.assertEqual(processes["authority-console"]["status"], "running_unmanaged")
        self.assertFalse(processes["authority-console"]["control_available"])
        self.assertEqual(processes["peer-link-server"]["status"], "stopped")
        self.assertTrue(processes["peer-link-server"]["control_available"])

    def test_control_basestation_process_payload_starts_and_stops_managed_peer_link(self) -> None:
        app = create_app(settings=self.settings)
        registry = LocalProcessRegistry(self.settings, workbench_host="127.0.0.1", workbench_port=18098)
        config = WorkbenchConfig(
            settings=self.settings,
            static_dir=Path("."),
            app=app,
            process_registry=registry,
            listen_host="127.0.0.1",
            listen_port=18098,
            started_at_utc="2026-09-07T00:00:00+00:00",
        )
        fake_process = _FakeManagedProcess(pid=45678)

        with patch("ehn_basestation.app.workbench_server._is_local_port_open", return_value=False), patch(
            "ehn_basestation.app.workbench_server.subprocess.Popen",
            return_value=fake_process,
        ):
            status, started = control_basestation_process_payload(
                config,
                {"process_id": "peer-link-server", "action": "start"},
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(started["process"]["status"], "running")
            self.assertEqual(started["process"]["pid"], 45678)

            status, stopped = control_basestation_process_payload(
                config,
                {"process_id": "peer-link-server", "action": "stop"},
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(stopped["process"]["status"], "stopped")
            self.assertTrue(fake_process.terminated)

    def test_workbench_identity_payload_reports_process_details(self) -> None:
        app = create_app(settings=self.settings)
        config = WorkbenchConfig(
            settings=self.settings,
            static_dir=Path("."),
            app=app,
            process_registry=LocalProcessRegistry(self.settings, workbench_host="127.0.0.1", workbench_port=18098),
            listen_host="0.0.0.0",
            listen_port=18098,
            started_at_utc="2026-09-07T12:34:56+00:00",
        )

        payload = build_workbench_identity_payload(config)

        self.assertEqual(payload["service"], "workbench-preview")
        self.assertEqual(payload["station_name"], self.settings.station_name)
        self.assertEqual(payload["listen_host"], "0.0.0.0")
        self.assertEqual(payload["listen_port"], 18098)
        self.assertEqual(payload["started_at_utc"], "2026-09-07T12:34:56+00:00")
        self.assertTrue(payload["module_path"].endswith("workbench_server.py"))

    def test_node_and_contacts_payloads_project_meshcore_shape(self) -> None:
        node = build_node_payload(self.settings, self.client)
        contacts = build_contacts_payload(self.client)

        self.assertEqual(node["prefs"]["node_name"], "pi-port-probe")
        self.assertEqual(node["prefs"]["tx_power_max_dbm"], 17)
        self.assertEqual(node["prefs"]["gps_mode"], "not_supported")
        self.assertFalse(node["prefs"]["gps_supported"])
        self.assertEqual(node["channels"][1]["name"], "private-test")
        self.assertEqual(contacts["recent_contacts"][0]["name"], "probe-contact")
        self.assertEqual(contacts["recent_contacts"][0]["out_path_len"], 2)

    def test_messages_and_logs_payloads_are_readable(self) -> None:
        messages = build_messages_payload(self.client)
        logs = build_logs_payload(self.settings, self.client)

        self.assertEqual(messages["messages"][0]["direction"], "sent")
        self.assertEqual(messages["messages"][1]["direction"], "received")
        self.assertEqual(
            messages["messages"][1]["source_id"],
            "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        )
        self.assertLessEqual(len(messages["messages"]), 50)
        self.assertTrue(any("gateway_id=" in line for line in logs["lines"]))

    def test_messages_payload_prefers_db_user_id_for_known_meshcore_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "0",
                    "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
                    "BASESTATION_MESHCORE_LINK_ENDPOINT": "127.0.0.1:7463",
                },
            )
            app = create_app(settings=settings)
            save_directory_user_payload(
                settings,
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                },
            )

            messages = build_messages_payload(self.client, app)

            inbound = next(item for item in messages["messages"] if item["direction"] == "received")
            self.assertEqual(inbound["source_id"], "n2dh")
            self.assertEqual(inbound["source_label"], "n2dh")
            self.assertEqual(
                inbound["source_identity"],
                "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            )
            self.assertTrue(any(item["label"] == "n2dh" for item in messages["registered_users"]))
            self.assertTrue(any(item["label"] == "n2dh" and item["state"] == "messaging" for item in messages["registered_users"]))

    def test_meshcore_private_inbound_routes_to_known_sms_target(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-N2DH",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "burner",
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "burner",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550177",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            self.client.list_gateway_inbound_events = lambda **kwargs: [
                {
                    "event_id": "private-rx-1",
                    "event_type": "contact_message_rx",
                    "event_ts_utc": "2026-09-06T20:00:00+00:00",
                    "transport": "meshcore-runtime",
                    "summary": "@burner heard the word",
                    "payload": {
                        "text": "@burner heard the word",
                        "message_scope": "direct",
                        "sender": {
                            "public_key": "PUBKEY-N2DH",
                            "short_name": "N2DH",
                        },
                        "raw": {
                            "channel_index": -1,
                            "message_scope": "direct",
                            "sender_public_key": "PUBKEY-N2DH",
                        },
                    },
                }
            ]

            messages = build_messages_payload(self.client, app)

            outbound = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=5)
            self.assertEqual(len(outbound), 1)
            self.assertEqual(outbound[0]["to_number"], "+15550177")
            self.assertEqual(outbound[0]["body"], "MeshCore n2dh: heard the word")
            self.assertIsNotNone(app.directory_store.find_user_by_identity_display_name("burner"))
            routed_event = next(item for item in messages["messages"] if item.get("target_id") == "burner")
            self.assertEqual(routed_event["source_id"], "n2dh")
            self.assertEqual(routed_event["source_label"], "n2dh")

    def test_private_channel_reply_fans_out_only_to_group_sms_members_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_SMS_ENABLED": "1"})
            app = create_app(settings=settings)
            for label, mode, enabled in (("group", "GROUP", True), ("both", "BOTH", True), ("direct", "DIRECT", True), ("disabled", "GROUP", False)):
                app.authority_directory_store.save_directory_user({
                    "user": {"user_id": label, "display_name": label, "status": "active"},
                    "identity": {"display_name": label, "messaging_mode": mode},
                    "endpoints": [{"network": "SMS", "native_address": f"+1555010{len(label)}", "enabled": enabled}],
                })
            self.client.list_gateway_inbound_events = lambda **kwargs: [
                {"event_type": "channel_message_rx", "event_ts_utc": "2026-09-28T12:00:00+00:00",
                 "payload": {"text": "OK lets go", "channel_index": 1, "message_scope": "channel"}},
                {"event_type": "channel_message_tx", "event_ts_utc": "2026-09-28T11:59:00+00:00",
                 "payload": {"text": "SMS Steve: net check", "channel_index": 1, "message_scope": "channel"}},
            ]

            with patch("ehn_basestation.app.workbench_server.MeshCoreRuntimeClient.from_settings", return_value=self.client):
                poll_meshcore_inbound_events(settings, app)
            build_messages_payload(self.client, app)

            rows = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=10)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["to_number"] for row in rows}, {"+15550105", "+15550104"})
            self.assertTrue(all(row["body"] == "MeshCore group from unknown MeshCore sender: OK lets go" for row in rows))

    def test_group_ingest_does_not_replay_old_channel_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_SMS_ENABLED": "1"})
            app = create_app(settings=settings)
            app.directory_store.save_directory_user({
                "user": {"user_id": "group", "display_name": "Group", "status": "active"},
                "identity": {"display_name": "group", "messaging_mode": "GROUP"},
                "endpoints": [{"network": "SMS", "native_address": "+15550101", "enabled": True}],
            })
            _initialize_meshcore_group_ingest(settings)
            self.client.list_gateway_inbound_events = lambda **kwargs: [
                {"event_type": "channel_message_rx", "event_ts_utc": "2000-01-01T00:00:00+00:00",
                 "payload": {"text": "old reply", "channel_index": 1, "message_scope": "channel"}},
                {"event_type": "channel_message_rx", "event_ts_utc": "9999-01-01T00:00:00+00:00",
                 "payload": {"text": "new reply", "channel_index": 1, "message_scope": "channel"}},
            ]

            with patch("ehn_basestation.app.workbench_server.MeshCoreRuntimeClient.from_settings", return_value=self.client):
                poll_meshcore_inbound_events(settings, app)
                poll_meshcore_inbound_events(settings, app)

            rows = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=10)
            self.assertEqual([row["body"] for row in rows], ["MeshCore group from unknown MeshCore sender: new reply"])

    def test_meshcore_group_ingress_fans_out_to_sms_and_meshtastic_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings, app = self._create_sms_group_test_app(Path(temp_dir))
            event = {
                "event_id": "tbeam-group-test-1",
                "event_type": "channel_message_rx",
                "event_ts_utc": "9999-01-01T00:00:00+00:00",
                "payload": {
                    "text": "TBEAM group status",
                    "channel_index": 1,
                    "message_scope": "channel",
                    "sender": {"public_key": "06d5ec920531551a851c0a1b72ac7fd2735b1b6a53fbf0316052f82a7c6444b9", "pubkey_prefix": "06d5ec920531"},
                },
            }
            self.client.list_gateway_inbound_events = lambda **kwargs: [event]
            self.client.list_observed_nodes = lambda: [{
                "public_key": "06d5ec920531551a851c0a1b72ac7fd2735b1b6a53fbf0316052f82a7c6444b9",
                "contact_name": "Tek2",
            }]
            with patch("ehn_basestation.app.workbench_server.MeshCoreRuntimeClient.from_settings", return_value=self.client), \
                    patch.object(app.meshtastic_broker, "send_text", return_value={
                        "accepted": True,
                        "status": "accepted_by_local_gateway",
                        "transport_message_id": "meshtastic-group-1",
                    }) as send_meshtastic:
                poll_meshcore_inbound_events(settings, app)
                poll_meshcore_inbound_events(settings, app)

            sms_rows = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=10)
            self.assertEqual({row["to_number"] for row in sms_rows}, {"+15550group", "+15550both", "+15550burner"})
            self.assertTrue(all(row["body"] == "MeshCore group from Tek2: TBEAM group status" for row in sms_rows))
            send_meshtastic.assert_called_once_with(
                text="MeshCore group from Tek2: TBEAM group status",
                destination_id="^all",
                channel_index=settings.meshtastic_group_channel_index,
                want_ack=False,
            )
            self.assertTrue(any(
                entry[0] == "meshtastic" and entry[2] == "MeshCore group from Tek2: TBEAM group status"
                for entry in app.group_forward_echoes
            ))

    def test_meshcore_group_sender_prefers_assigned_identity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={})
            app = create_app(settings=settings)
            public_key = "9b4994708df92595a2af9f70ee88b4b3a40e95eebffe49dd1757d18d36d373cc"
            app.directory_store.save_directory_user({
                "user": {"user_id": "user-observed-tbeam", "display_name": "N2DH-TBEAM", "status": "observed"},
                "identity": {"display_name": "tbeam", "messaging_mode": "BOTH"},
                "endpoints": [{"network": "MESHCORE", "native_address": public_key, "enabled": True}],
            })
            self.client.list_observed_nodes = lambda: [{"public_key": public_key, "contact_name": "N2DH-TBEAM"}]
            events = [
                {"payload": {"text": "N2DH-TBEAM: My radio check", "message_scope": "channel"}},
                {"payload": {"text": "My radio check", "sender": {"public_key": public_key, "display_name": "N2DH-TBEAM"}}},
                {"payload": {"text": "My radio check", "sender": {"pubkey_prefix": public_key[:12]}}},
            ]
            for event in events:
                with self.subTest(event=event):
                    self.assertEqual(_meshcore_group_sender_label(app, self.client, event), "tbeam")

    def test_meshcore_group_sender_does_not_guess_ambiguous_name(self) -> None:
        self.client.list_observed_nodes = lambda: [
            {"public_key": "a" * 64, "contact_name": "Shared name"},
            {"public_key": "b" * 64, "contact_name": "Shared name"},
        ]
        with TemporaryDirectory() as temp_dir:
            app = create_app(settings=BasestationSettings.from_env(project_root=Path(temp_dir), env={}))
            event = {"payload": {"text": "Shared name: radio check", "message_scope": "channel"}}
            self.assertEqual(_meshcore_group_sender_label(app, self.client, event), "unknown MeshCore sender")

    def test_meshcore_private_inbound_to_unknown_user_is_not_queued(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            self.client.list_gateway_inbound_events = lambda **kwargs: [
                {
                    "event_id": "private-rx-unknown-1",
                    "event_type": "contact_message_rx",
                    "event_ts_utc": "2026-09-13T20:00:00+00:00",
                    "transport": "meshcore-runtime",
                    "summary": "@ghost anyone there",
                    "payload": {
                        "text": "@ghost anyone there",
                        "message_scope": "direct",
                        "sender": {"public_key": "PUBKEY-UNKNOWN", "short_name": "UNK"},
                        "raw": {"channel_index": -1, "message_scope": "direct", "sender_public_key": "PUBKEY-UNKNOWN"},
                    },
                }
            ]

            build_messages_payload(self.client, app)

            self.assertEqual(app.delivery_store.list_pending_messages(), [])
            statuses = [event.get("status") for event in app.message_events.list_events()]
            self.assertIn("unknown_target", statuses)
            self.assertNotIn("accepted_but_queued", statuses)

    def test_meshcore_private_inbound_is_not_resent_on_refresh(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-N2DH",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "burner",
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "burner",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550177",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            inbound_events = [
                {
                    "event_id": "private-rx-1",
                    "event_type": "contact_message_rx",
                    "event_ts_utc": "2026-09-06T20:00:00+00:00",
                    "transport": "meshcore-runtime",
                    "summary": "@burner heard the word",
                    "payload": {
                        "text": "@burner heard the word",
                        "message_scope": "direct",
                        "sender": {
                            "public_key": "PUBKEY-N2DH",
                            "short_name": "N2DH",
                        },
                        "raw": {
                            "channel_index": -1,
                            "message_scope": "direct",
                            "sender_public_key": "PUBKEY-N2DH",
                        },
                    },
                }
            ]
            self.client.list_gateway_inbound_events = lambda **kwargs: list(inbound_events)

            build_messages_payload(self.client, app)
            build_messages_payload(self.client, app)

            outbound = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=5)
            self.assertEqual(len(outbound), 1)
            delivery_events = [
                event for event in app.message_events.list_events()
                if event.get("event_type") == "delivery_dispatch" and event.get("target_id") == "burner"
            ]
            self.assertEqual(len(delivery_events), 1)

    def test_registered_users_omits_unresolved_meshcore_contact_node_ids(self) -> None:
        messages = build_messages_payload(self.client)

        self.assertFalse(any(item["label"] == "probe-contact" for item in messages["registered_users"]))
        self.assertFalse(any(str(item["label"]).endswith("3ad31a99") for item in messages["registered_users"]))
        self.assertFalse(any(item["label"] == "private-test" for item in messages["registered_users"]))

    def test_registered_users_marks_known_sms_register_sender_as_registered(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "field-tester",
                        "display_name": "Field Tester",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "field-tester",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550101",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "body": "register",
                    "transport": "sms",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["action"], "register")
            messages = build_messages_payload(self.client, app)
            self.assertTrue(any(item["label"] == "field-tester" and item["state"] == "registered" for item in messages["registered_users"]))

    def test_meshcore_gateway_payload_projects_private_channels_and_history(self) -> None:
        payload = build_meshcore_gateway_payload(self.settings, self.client)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["private_channel_count"], 1)
        self.assertEqual(payload["private_channels"][0]["name"], "private-test")
        self.assertEqual(payload["contacts"][0]["name"], "probe-contact")

    def test_meshcore_private_channel_send_payload_uses_runtime_client(self) -> None:
        status, payload = send_meshcore_private_channel_message_payload(
            self.client,
            {"channel_index": 1, "channel_name": "private-test", "text": "hello channel"},
        )

        self.assertEqual(status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["channel_index"], 1)

    def test_sms_gateway_send_payload_queues_outbound_sms(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                },
            )
            app = create_app(settings=settings)

            status, payload = send_sms_gateway_message_payload(
                app,
                {"to_number": "+15550199", "body": "Outbound SMS test"},
            )

            self.assertEqual(status.value, 200)
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["payload"]["to_number"], "+15550199")

    def test_save_directory_user_payload_persists_identity_rcs_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(project_root=root)

            status, payload = save_directory_user_payload(
                settings,
                {
                    "user": {
                        "display_name": "Probe User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PENDING_AUTHORITY_REVIEW",
                    },
                    "identity": {
                        "display_name": "probe-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "personal": {
                            "phone_primary": "+15550001111",
                        },
                        "identity": {
                            "routing_notes": "probe-note",
                            "rcs_name": "Probe Visible Name",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550001111",
                            "enabled": True,
                            "verified": False,
                            "priority": 80,
                            "trust_level": "draft",
                            "replication_class": "TRUSTED_STATION",
                            "capability_tags": "direct",
                        }
                    ],
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["entry"]["profile"]["identity"]["rcs_name"], "Probe Visible Name")

    def test_save_directory_user_payload_returns_conflict_for_duplicate_endpoint_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(project_root=root)

            first_status, first_payload = save_directory_user_payload(
                settings,
                {
                    "user": {
                        "display_name": "First User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PENDING_AUTHORITY_REVIEW",
                    },
                    "identity": {
                        "display_name": "first-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "endpoint_id": "endpoint-dup",
                            "network": "SMS",
                            "native_address": "+15550000001",
                            "enabled": True,
                            "verified": False,
                            "priority": 80,
                            "trust_level": "draft",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                },
            )

            duplicate_status, duplicate_payload = save_directory_user_payload(
                settings,
                {
                    "user": {
                        "display_name": "Second User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PENDING_AUTHORITY_REVIEW",
                    },
                    "identity": {
                        "display_name": "second-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "endpoint_id": "endpoint-dup",
                            "network": "SMS",
                            "native_address": "+15550000002",
                            "enabled": True,
                            "verified": False,
                            "priority": 80,
                            "trust_level": "draft",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                },
            )

            self.assertEqual(first_status, HTTPStatus.OK)
            self.assertTrue(first_payload["ok"])
            self.assertEqual(duplicate_status, HTTPStatus.CONFLICT)
            self.assertFalse(duplicate_payload["ok"])
            self.assertIn("UNIQUE constraint failed", duplicate_payload["error"])

    def test_messages_payload_includes_basestation_sms_events(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550100",
                    "body": "@alex-2000 Need pickup",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-02T12:00:00+00:00",
                },
            )

            messages = build_messages_payload(self.client, app)

            self.assertGreaterEqual(messages["message_count"], 3)
            self.assertTrue(any(entry["detail"].startswith("sms • route") for entry in messages["messages"]))
            self.assertEqual(messages["sms_gateway"]["gateway_id"], "EHN-NNJ")

    def test_inbound_sms_routes_direct_message_to_meshcore_user_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.delivery_orchestrator.send_directed_private = self._capture_direct_delivery
            save_directory_user_payload(
                settings,
                {
                    "user": {
                        "user_id": "sms-jane",
                        "display_name": "Jane Private",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "sms-jane",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550100",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                },
            )
            save_directory_user_payload(
                settings,
                {
                    "user": {
                        "user_id": "alex-2000",
                        "display_name": "Alex Example",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "alex-2000",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-ALEX",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                },
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550100",
                    "from_name": "Jane",
                    "body": "@alex-2000 Need pickup",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-03T12:00:00+00:00",
                },
                settings=settings,
                meshcore_client=self.client,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["mode"], "direct")
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            self.assertIsNotNone(self.last_delivery_request)
            self.assertEqual(self.last_delivery_request.destination["public_key"], "PUBKEY-ALEX")
            self.assertEqual(self.last_delivery_request.payload["text"], "SMS sms-jane: Need pickup")
            gateway = app.gateway_registry.find_provider_for_network("sms")
            self.assertEqual(gateway.list_inbound_events(limit=1)[0]["sender_user_id"], "sms-jane")

    def test_inbound_sms_routes_direct_message_to_meshtastic_user_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(
                project_root=Path(temp_dir),
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_MESHTASTIC_ENABLED": "1",
                    "BASESTATION_MESHTASTIC_GATEWAY_ID": "meshtastic-office",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.delivery_orchestrator.send_directed_private = self._capture_direct_delivery
            app.directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "sms-jane",
                        "display_name": "Jane Private",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "sms-jane",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550100",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            app.directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "meshtastic-alex",
                        "display_name": "Alex Meshtastic",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "meshtastic-alex",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHTASTIC",
                            "native_address": "!a1ad6a0c",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550100",
                    "from_name": "Jane",
                    "body": "@meshtastic-alex Need pickup",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-03T12:00:00+00:00",
                },
                settings=settings,
                meshcore_client=self.client,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["network"], "meshtastic")
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            self.assertEqual(self.last_delivery_request.destination["native_address"], "!a1ad6a0c")
            self.assertEqual(self.last_delivery_request.payload["text"], "SMS sms-jane: Need pickup")

    def test_inbound_rcs_routes_direct_message_to_meshcore_user_by_identity_label(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.delivery_orchestrator.send_directed_private = self._capture_direct_delivery
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "display_name": "Steve Phone",
                        "status": "active",
                        "home_region": "NJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "steve",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {
                            "rcs_name": "Stephen Wilcox",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550100",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-N2DH",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550100",
                    "from_name": "Stephen Wilcox",
                    "body": "@n2dh is the best thing",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-06T19:32:38.504674Z",
                },
                settings=settings,
                meshcore_client=self.client,
            )

            self.assertEqual(status.value, 200)
            self.assertIsNotNone(payload["meshcore_delivery"])
            self.assertEqual(payload["meshcore_delivery"]["mode"], "direct")
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            self.assertIsNotNone(self.last_delivery_request)
            self.assertEqual(self.last_delivery_request.destination["public_key"], "PUBKEY-N2DH")
            self.assertEqual(self.last_delivery_request.payload["text"], "SMS steve: is the best thing")

    def test_inbound_sms_routes_direct_message_to_authority_user_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.delivery_orchestrator.send_directed_private = self._capture_direct_delivery
            save_directory_user_payload(
                settings,
                {
                    "user": {
                        "user_id": "field-tester",
                        "display_name": "Field Tester Private",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "field-tester",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550101",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                },
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "personal": {
                            "given_name": "Stephen",
                            "family_name": "Wilcox",
                            "member_status": "Member",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-N2DH",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "from_name": "Field Tester",
                    "body": "@n2dh Status update",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-05T16:00:00+00:00",
                },
                settings=settings,
                meshcore_client=self.client,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["mode"], "direct")
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            self.assertEqual(payload["meshcore_delivery"]["target_id"], "n2dh")
            self.assertIsNotNone(self.last_delivery_request)
            self.assertEqual(self.last_delivery_request.destination["public_key"], "PUBKEY-N2DH")
            self.assertEqual(self.last_delivery_request.payload["text"], "SMS field-tester: Status update")
            self.assertIsNotNone(app.directory_store.get_user_by_id("n2dh"))
            gateway = app.gateway_registry.find_provider_for_network("sms")
            self.assertEqual(gateway.list_inbound_events(limit=1)[0]["sender_user_id"], "field-tester")

    def test_inbound_sms_prefers_sms_route_when_delivery_policy_prefers_sms(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "field-tester",
                        "display_name": "Field Tester",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "field-tester",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550101",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-N2DH",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        },
                        {
                            "network": "SMS",
                            "native_address": "+15550199",
                            "enabled": True,
                            "verified": True,
                            "priority": 90,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        },
                    ],
                }
            )
            app.delivery_store.upsert_delivery_policy(
                user_id="n2dh",
                preferred_routes=["sms", "meshcore"],
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "body": "@n2dh Status update",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-06T13:00:00+00:00",
                },
                settings=settings,
                meshcore_client=None,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["network"], "sms")
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            gateway = app.gateway_registry.find_provider_for_network("sms")
            outbound = gateway.list_outbound_messages(limit=5)
            self.assertEqual(outbound[0]["to_number"], "+15550199")
            self.assertEqual(outbound[0]["body"], "SMS field-tester: Status update")

    def test_inbound_identity_label_target_is_queued_by_canonical_user_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={
                "BASESTATION_MESHCORE_ENABLED": "0",
                "BASESTATION_MESHTASTIC_ENABLED": "1",
            })
            app = create_app(settings=settings)
            target = app.directory_store.save_directory_user({
                "user": {"user_id": "user-tbeam", "display_name": "TBEAM", "status": "observed", "retention_class": "FIELD_OBSERVED"},
                "identity": {"display_name": "tbeam", "identity_type": "USER", "messaging_mode": "BOTH"},
                "endpoints": [{"network": "MESHCORE", "native_address": "PUBKEY-TBEAM", "enabled": True}],
            })
            routing_result = SimpleNamespace(
                classification=SimpleNamespace(
                    action="route", is_valid_ehn=True,
                    target=SimpleNamespace(target_type="user", target_id="tbeam"),
                    sender_endpoint="!sender", body="reply text", content_class="message",
                    conversation_scope="single",
                ),
                sender_entry=None,
                target_entry=target,
            )

            with patch("ehn_basestation.app.workbench_server._select_target_delivery_endpoint", return_value=("meshcore", None)):
                result = route_inbound_private_to_user_payload(
                    app, routing_result=routing_result, gateway_label="Meshtastic", default_network="meshtastic",
                )

            pending = app.delivery_store.list_pending_messages(include_content=False)
            self.assertEqual(result["status"], "accepted_but_queued")
            self.assertEqual(pending[0]["target_user_id"], "user-tbeam")
            self.assertEqual(pending[0]["selected_route"], "meshcore")

    def test_inbound_sms_queues_known_target_and_replies_message_stored_when_no_route_exists(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "field-tester",
                        "display_name": "Field Tester",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "field-tester",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "personal": {
                            "preferred_language": "en",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550101",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [],
                }
            )
            app.delivery_store.upsert_delivery_policy(
                user_id="n2dh",
                preferred_routes=["meshcore"],
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "body": "@n2dh Status update",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-06T13:05:00+00:00",
                },
                settings=settings,
                meshcore_client=None,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["status"], "accepted_but_queued")
            self.assertTrue(payload["reply_queued"])
            self.assertEqual(payload["registration_reply"], "message stored")
            pending_for_target = [
                item for item in app.delivery_store.list_pending_messages()
                if item.get("target_user_id") == "n2dh"
            ]
            self.assertEqual(len(pending_for_target), 1)
            gateway = app.gateway_registry.find_provider_for_network("sms")
            outbound = gateway.list_outbound_messages(limit=5)
            self.assertEqual(outbound[0]["to_number"], "+15550101")
            self.assertEqual(outbound[0]["body"], "message stored")

    def test_sms_gateway_payload_exposes_resolved_display_name_for_inbound_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "burner",
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "burner",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {"rcs_name": "Stephen Wilcox Burner"},
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550177",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            receive_android_sms_inbound_payload(
                app,
                {
                    "from_name": "Stephen Wilcox Burner",
                    "body": "@n2dh from my way home",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-05T22:40:30.349211Z",
                },
            )

            status, payload = build_sms_gateway_payload(app)

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["inbound_events"][0]["sender_user_id"], "burner")
            self.assertEqual(payload["inbound_events"][0]["sender_label"], "burner")

    def test_messages_payload_uses_identity_label_for_known_rcs_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "user-d0579cb1c069",
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "burner",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {"rcs_name": "Stephen Wilcox Burner"},
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550177",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            receive_android_sms_inbound_payload(
                app,
                {
                    "from_name": "Stephen Wilcox Burner",
                    "body": "@n2dh from my way home",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-05T22:40:30.349211Z",
                },
            )

            messages = build_messages_payload(self.client, app)
            inbound = next(item for item in messages["messages"] if item["network"] == "rcs")

            self.assertEqual(inbound["source_id"], "user-d0579cb1c069")
            self.assertEqual(inbound["source_label"], "burner")

    def test_meshcore_gateway_payload_includes_routed_delivery_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "field-tester",
                        "display_name": "Field Tester",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "field-tester",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550101",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        },
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-FIELD",
                            "enabled": True,
                            "verified": True,
                            "priority": 90,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        },
                    ],
                }
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "n2dh",
                        "display_name": "N2DH",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "n2dh",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "PUBKEY-N2DH",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            meshcore_gateway = app.gateway_registry.find_provider_for_network("meshcore")
            self.assertIsNotNone(meshcore_gateway)

            with patch.object(
                type(meshcore_gateway),
                "send_directed_private",
                new=lambda _adapter, request: self._capture_direct_delivery(network="meshcore", request=request),
            ):
                receive_android_sms_inbound_payload(
                    app,
                    {
                        "from_number": "+15550101",
                        "body": "@n2dh Status update",
                        "transport": "sms",
                        "observed_at_utc": "2026-09-05T16:00:00+00:00",
                    },
                    settings=settings,
                    meshcore_client=self.client,
                )

            payload = build_meshcore_gateway_payload(settings, self.client, app)
            routed_event = next(item for item in payload["history"] if item.get("target_id") == "n2dh")

            self.assertEqual(routed_event["source_id"], "field-tester")
            self.assertEqual(routed_event["source_label"], "field-tester")
            self.assertEqual(routed_event["target_id"], "n2dh")

    def test_inbound_sms_routes_group_message_to_first_private_meshcore_channel(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            for label, mode, number in (("group", "GROUP", "+15550101"), ("both", "BOTH", "+15550102"), ("direct", "DIRECT", "+15550103")):
                app.authority_directory_store.save_directory_user({
                    "user": {"user_id": label, "display_name": label, "status": "active"},
                    "identity": {"display_name": label, "messaging_mode": mode},
                    "endpoints": [{"network": "SMS", "native_address": number, "enabled": True}],
                })

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550100",
                    "body": "& Shelter supplies arrived",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-03T12:05:00+00:00",
                },
                settings=settings,
                meshcore_client=self.client,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["mode"], "group")
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            self.assertEqual(payload["meshcore_delivery"]["channel_index"], 1)
            self.assertEqual(payload["meshcore_delivery"]["sms_fanout_count"], 2)
            self.assertIsNotNone(self.client.last_channel_message)
            # Provisional senders are shown by what the phone saw, not by their auto-generated id.
            self.assertEqual(self.client.last_channel_message["text"], "SMS +15550100: Shelter supplies arrived")
            outbound = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=10)
            self.assertEqual({item["to_number"] for item in outbound}, {"+15550101", "+15550102"})

    def test_sms_group_reaches_meshtastic_and_sms_when_meshcore_raises(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={
                "BASESTATION_SMS_ENABLED": "1", "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                "BASESTATION_MESHCORE_ENABLED": "1", "BASESTATION_MESHTASTIC_ENABLED": "1",
            })
            app = create_app(settings=settings)
            app.directory_store.save_directory_user({
                "user": {"user_id": "steve", "display_name": "Steve", "status": "active"},
                "identity": {"display_name": "steve", "messaging_mode": "BOTH"},
                "endpoints": [{"network": "SMS", "native_address": "+15550102", "enabled": True}],
            })
            with patch.object(app.meshtastic_broker, "send_text", return_value={
                "accepted": True, "status": "accepted_by_local_gateway", "transport_message_id": "123",
            }) as send, patch.object(self.client, "send_channel_message", side_effect=TimeoutError("Pi offline")):
                _, payload = receive_android_sms_inbound_payload(
                    app, {"from_number": "+15550100", "body": "& group test", "transport": "sms"},
                    settings=settings, meshcore_client=self.client,
                )
            send.assert_called_once_with(
                text="SMS +15550100: group test", destination_id="^all", channel_index=1, want_ack=False,
            )
            self.assertTrue(payload["meshtastic_delivery"]["accepted"])
            self.assertEqual(payload["meshcore_delivery"]["status"], "failed")
            self.assertEqual(payload["group_delivery"]["sms"]["queued_count"], 1)

    def _create_sms_group_test_app(self, root: Path, *, meshcore_enabled: bool = True):
        settings = BasestationSettings.from_env(project_root=root, env={
            "BASESTATION_SMS_ENABLED": "1", "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
            "BASESTATION_MESHCORE_ENABLED": "1" if meshcore_enabled else "0",
            "BASESTATION_MESHTASTIC_ENABLED": "1",
        })
        app = create_app(settings=settings)
        for label, mode, enabled, status in (
            ("burner", "BOTH", True, "active"), ("group", "GROUP", True, "active"),
            ("both", "BOTH", True, "active"), ("direct", "DIRECT", True, "active"),
            ("disabled", "GROUP", False, "active"), ("inactive", "GROUP", True, "inactive"),
        ):
            app.directory_store.save_directory_user({
                "user": {"user_id": label, "display_name": label, "status": status, "retention_class": "TRUSTED_STATION"},
                "identity": {"display_name": label, "messaging_mode": mode},
                "endpoints": [{"network": "SMS", "native_address": f"+15550{label}", "enabled": enabled}],
            })
        return settings, app

    def test_sms_group_dispatches_to_both_radios_and_eligible_sms_members_without_echo(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings, app = self._create_sms_group_test_app(Path(temp_dir))
            app.directory_store.create_provisional_sender(
                display_name="Observed Group Node",
                network="SMS",
                native_address="+15550observed",
                actor_class="person",
                messaging_mode="GROUP",
            )
            observed = app.directory_store.find_user_by_endpoint(
                network="SMS", native_address="+15550observed",
            )
            observed_payload = observed.as_dict()
            observed_payload["endpoints"][0]["enabled"] = True
            app.directory_store.save_directory_user(observed_payload)
            inbound = {"from_number": "+15550burner", "body": "& group test", "transport": "sms",
                       "observed_at_utc": "2026-10-03T20:00:00+00:00"}
            with patch.object(app.meshtastic_broker, "send_text", return_value={
                "accepted": True, "status": "accepted_by_local_gateway", "transport_message_id": "123",
            }) as send:
                _, payload = receive_android_sms_inbound_payload(app, inbound, settings=settings, meshcore_client=self.client)
                _, duplicate = receive_android_sms_inbound_payload(
                    app, {**inbound, "transport": "rcs", "observed_at_utc": "2026-10-03T20:00:20+00:00"},
                    settings=settings, meshcore_client=self.client,
                )
            self.assertTrue(duplicate["duplicate_suppressed"])
            send.assert_called_once_with(text="SMS burner: group test", destination_id="^all", channel_index=1, want_ack=False)
            self.assertEqual(self.client.last_channel_message["text"], "SMS burner: group test")
            self.assertEqual(payload["group_delivery"]["sms"]["queued_count"], 3)
            self.assertTrue(payload["group_delivery"]["meshcore"]["accepted"])
            self.assertTrue(payload["group_delivery"]["meshtastic"]["accepted"])
            echo = {"event_type": "channel_message_rx", "event_ts_utc": "2026-10-03T20:01:00+00:00",
                    "payload": {"channel_index": 1, "text": "SMS burner: group test", "message_scope": "channel"}}
            with patch.object(self.client, "list_gateway_inbound_events", return_value=[echo]), \
                    patch("ehn_basestation.app.workbench_server.MeshCoreRuntimeClient.from_settings", return_value=self.client):
                poll_meshcore_inbound_events(settings, app)
                poll_meshcore_inbound_events(settings, app)
            with patch.object(app.meshtastic_broker, "drain_inbound_events", return_value=[{
                "event_id": "echo-123", "message_class": "channel_message", "channel_index": 1,
                "text": "SMS burner: group test", "sender_endpoint": "!70117298",
            }]):
                poll_meshtastic_inbound_events(app)
            rows = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=20)
            self.assertEqual({row["to_number"] for row in rows}, {"+15550group", "+15550both", "+15550observed"})
            self.assertEqual(len(rows), 3)

    def test_sms_group_meshtastic_failures_do_not_block_meshcore_or_sms(self) -> None:
        for failure in ({"accepted": False, "status": "unavailable", "detail": "USB offline"}, RuntimeError("USB offline")):
            with self.subTest(failure=failure), TemporaryDirectory() as temp_dir:
                settings, app = self._create_sms_group_test_app(Path(temp_dir))
                options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(app.meshtastic_broker, "send_text", **options):
                    _, payload = receive_android_sms_inbound_payload(
                        app, {"from_number": "+15550burner", "body": "& group test", "transport": "sms"},
                        settings=settings, meshcore_client=self.client,
                    )
                self.assertFalse(payload["meshtastic_delivery"]["accepted"])
                self.assertTrue(payload["meshcore_delivery"]["accepted"])
                self.assertEqual(payload["group_delivery"]["sms"]["queued_count"], 2)
                self.assertFalse(any(entry[0] == "meshtastic" for entry in app.group_forward_echoes))

    def test_sms_group_queue_failure_does_not_block_radios(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings, app = self._create_sms_group_test_app(Path(temp_dir))
            with patch("ehn_basestation.app.workbench_server._queue_group_sms", side_effect=RuntimeError("Queue unavailable")), \
                    patch.object(app.meshtastic_broker, "send_text", return_value={"accepted": True, "status": "accepted_by_local_gateway"}):
                _, payload = receive_android_sms_inbound_payload(
                    app, {"from_number": "+15550burner", "body": "& group test", "transport": "sms"},
                    settings=settings, meshcore_client=self.client,
                )
            self.assertEqual(payload["group_delivery"]["sms"]["status"], "failed")
            self.assertIsNone(payload["group_delivery"]["sms"]["queued_count"])
            self.assertTrue(payload["meshcore_delivery"]["accepted"])
            self.assertTrue(payload["meshtastic_delivery"]["accepted"])

    def test_sms_group_without_meshcore_still_reaches_meshtastic_and_sms(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings, app = self._create_sms_group_test_app(Path(temp_dir), meshcore_enabled=False)
            with patch.object(app.meshtastic_broker, "send_text", return_value={"accepted": True, "status": "accepted_by_local_gateway"}), \
                    patch.object(self.client, "send_channel_message") as meshcore_send:
                _, payload = receive_android_sms_inbound_payload(
                    app, {"from_number": "+15550burner", "body": "& group test", "transport": "sms"},
                    settings=settings, meshcore_client=self.client,
                )
            meshcore_send.assert_not_called()
            self.assertEqual(payload["meshcore_delivery"]["status"], "disabled")
            self.assertTrue(payload["meshtastic_delivery"]["accepted"])
            self.assertEqual(payload["group_delivery"]["sms"]["queued_count"], 2)

    def test_sms_group_radio_plaintext_rejection_does_not_block_sms(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings, app = self._create_sms_group_test_app(Path(temp_dir))
            with patch.object(app.meshtastic_broker, "send_text") as send, patch.object(self.client, "send_channel_message") as meshcore_send:
                _, payload = receive_android_sms_inbound_payload(
                    app, {"from_number": "+15550burner", "body": "& caf\u00e9", "transport": "sms"},
                    settings=settings, meshcore_client=self.client,
                )
            send.assert_not_called()
            meshcore_send.assert_not_called()
            self.assertFalse(payload["meshtastic_delivery"]["accepted"])
            self.assertFalse(payload["meshcore_delivery"]["accepted"])
            self.assertEqual(payload["group_delivery"]["sms"]["queued_count"], 2)

    def test_sms_group_policy_and_nonlocal_target_do_not_broadcast(self) -> None:
        for sender, body, reason in (("direct", "& blocked", "sender_mode_not_allowed"), ("burner", "&OTHER blocked", None)):
            with self.subTest(sender=sender), TemporaryDirectory() as temp_dir:
                settings, app = self._create_sms_group_test_app(Path(temp_dir))
                with patch.object(app.meshtastic_broker, "send_text") as send, patch.object(self.client, "send_channel_message") as meshcore_send:
                    _, payload = receive_android_sms_inbound_payload(
                        app, {"from_number": f"+15550{sender}", "body": body, "transport": "sms"},
                        settings=settings, meshcore_client=self.client,
                    )
                send.assert_not_called()
                meshcore_send.assert_not_called()
                self.assertIsNone(payload["group_delivery"])
                if reason:
                    self.assertEqual(payload["reason"], reason)
                else:
                    self.assertEqual(payload["meshcore_delivery"]["status"], "non_local_group")

    def test_zero_hop_advert_checks_connection_capability_and_transmit_permission(self) -> None:
        for connected, supported, allowed, expected in (
            (False, True, True, HTTPStatus.SERVICE_UNAVAILABLE),
            (True, False, True, HTTPStatus.NOT_IMPLEMENTED),
            (True, True, False, HTTPStatus.FORBIDDEN),
            (True, True, True, HTTPStatus.OK),
        ):
            with self.subTest(connected=connected, supported=supported, allowed=allowed):
                with patch.object(self.client, "get_snapshot", return_value={
                    "connected": connected,
                    "runtime_status": {"zero_hop_advert_supported": supported, "gateway_tx_allowed": allowed},
                }), patch.object(self.client, "send_zero_hop_advert", create=True, return_value=(True, "Local advert accepted.", {"flood": False})) as advert:
                    status, response = send_meshcore_zero_hop_advert_payload(self.client)
                self.assertEqual(status, expected)
                if expected == HTTPStatus.OK:
                    advert.assert_called_once_with()
                    self.assertFalse(response["payload"]["flood"])
                else:
                    advert.assert_not_called()

    def test_meshcore_event_normalization_preserves_advertised_sender_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_MESHCORE_ENABLED": "0"})
            normalized = _normalize_meshcore_event_for_routing(settings, {
                "event_type": "contact_message_rx",
                "payload": {
                    "text": "@burner check in",
                    "message_scope": "direct",
                    "sender": {"public_key": "12" * 32, "display_name": "N@DH-TBEAM"},
                },
            })
            self.assertEqual(normalized.sender_endpoint, "12" * 32)
            self.assertEqual(normalized.sender_display_name, "N@DH-TBEAM")

    def test_meshcore_contacts_refresh_enriches_unknown_directory_user(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_MESHCORE_ENABLED": "1"})
            app = create_app(settings=settings)
            public_key = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
            observed = app.directory_store.create_provisional_sender(
                display_name="Unknown MESHCORE sender", network="MESHCORE", native_address=public_key,
                actor_class="person", messaging_mode="DIRECT",
            )

            build_meshcore_gateway_payload(settings, self.client, app)

            updated = app.directory_store.get_user_by_id(observed.user.user_id)
            self.assertEqual(updated.user.display_name, "probe-contact")
            self.assertEqual(updated.endpoints[0].native_address, public_key)

    def test_processed_meshcore_event_can_enrich_placeholder_without_rerouting(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(
                project_root=Path(temp_dir), env={"BASESTATION_MESHCORE_ENABLED": "1"},
            )
            app = create_app(settings=settings)
            public_key = "56" * 32
            observed = app.directory_store.create_provisional_sender(
                display_name="Unknown MESHCORE sender", network="MESHCORE", native_address=public_key,
                actor_class="person", messaging_mode="DIRECT",
            )
            event = {
                "event_id": "already-routed-1", "event_type": "contact_message_rx",
                "event_ts_utc": "2026-10-09T23:00:00+00:00",
                "payload": {"text": "@burner already routed", "message_scope": "direct",
                            "sender": {"public_key": public_key, "display_name": "N2DH-TBEAM"}},
            }
            with patch.object(self.client, "list_gateway_inbound_events", return_value=[event]), \
                    patch("ehn_basestation.app.workbench_server.MeshCoreRuntimeClient.from_settings", return_value=self.client), \
                    patch("ehn_basestation.app.workbench_server._load_meshcore_ingest_state", return_value={
                        "processed_private_event_keys": ["id:already-routed-1"], "group_ingest_since": "2026-10-09T00:00:00+00:00",
                    }):
                poll_meshcore_inbound_events(settings, app)
            updated = app.directory_store.get_user_by_id(observed.user.user_id)
            self.assertEqual(updated.user.display_name, "N2DH-TBEAM")
            self.assertEqual(app.delivery_store.list_open_messages(), [])
            self.assertIsNone(self.client.last_channel_message)

    def test_meshtastic_name_only_identity_is_available_to_provisional_sender(self) -> None:
        class _Router:
            def _normalize_gateway_event(self, gateway_id, event, *, network):
                normalized = dict(event)
                normalized.update(gateway_id=gateway_id, network=network)
                return SimpleNamespace(**normalized)

            def ingest_inbound_text_message(self, message):
                self.message = message
                return SimpleNamespace(classification=SimpleNamespace(conversation_scope="single", action="log_only"))

        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_MESHCORE_ENABLED": "0"})
            app = create_app(settings=settings)
            app.inbound_router = _Router()
            inbound_event = {
                "event_id": "rx-name-1", "event_type": "text_rx", "direction": "received",
                "message_class": "direct_message", "sender_endpoint": "!a1ad6a0c",
                "sender_display_name": "N@DH-TBEAM", "text": "@burner check in",
            }

            class _Broker:
                gateway_id = "meshtastic-test"

                def drain_inbound_events(self, *, limit=50):
                    return [inbound_event]

            broker = _Broker()
            app.meshtastic_broker = broker
            app.gateway_registry.register(MeshtasticGatewayAdapter(broker=broker))

            poll_meshtastic_inbound_events(app)
            self.assertEqual(app.inbound_router.message.sender_endpoint, "!a1ad6a0c")
            self.assertEqual(app.inbound_router.message.sender_display_name, "N@DH-TBEAM")

    def test_unavailable_station_group_does_not_transmit_and_replies(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_SMS_ENABLED": "1", "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ"})
            app = create_app(settings=settings)
            self.client.last_channel_message = None

            _, payload = receive_android_sms_inbound_payload(
                app, {"from_number": "+15550100", "body": "&OTHER hello", "transport": "sms"},
                settings=settings, meshcore_client=self.client,
            )

            self.assertEqual(payload["meshcore_delivery"]["status"], "non_local_group")
            self.assertEqual(payload["registration_reply"], "group not available")
            self.assertIsNone(self.client.last_channel_message)

    def test_unknown_rcs_sender_header_uses_rcs_name_but_directory_keeps_canonical_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_name": "Pat Newcomer",
                    "body": "& First time on the net",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-13T12:08:00+00:00",
                },
                settings=settings,
                meshcore_client=self.client,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["sender_resolution"], "provisional")
            self.assertEqual(self.client.last_channel_message["text"], "SMS Pat Newcomer: First time on the net")

            provisional = app.directory_store.find_user_by_endpoint(network="SMS", native_address="Pat Newcomer")
            if provisional is None:
                provisional = app.directory_store.find_user_by_rcs_name("Pat Newcomer")
            self.assertIsNotNone(provisional)
            self.assertRegex(provisional.user.user_id, r"^user-[a-f0-9]{12}$")
            self.assertEqual(provisional.identity.display_name, provisional.user.user_id)
            self.assertEqual(provisional.user.display_name, "Pat Newcomer")
            self.assertEqual(provisional.user.retention_class, "FIELD_OBSERVED")
            self.assertEqual(provisional.profile["identity"]["rcs_name"], "Pat Newcomer")
            self.assertEqual(provisional.endpoints, ())
            self.assertIsNone(app.directory_store.find_user_by_endpoint(network="SMS", native_address="Pat Newcomer"))

            # Once promoted, the canonical label takes over the header.
            promoted = provisional.as_dict()
            promoted["user"]["retention_class"] = "TRUSTED_STATION"
            promoted["user"]["promotion_status"] = "PROMOTED"
            promoted["identity"]["display_name"] = "pat"
            app.directory_store.save_directory_user(promoted)
            receive_android_sms_inbound_payload(
                app,
                {"from_name": "Pat Newcomer", "body": "& Second message", "transport": "rcs",
                 "observed_at_utc": "2026-09-13T12:09:00+00:00"},
                settings=settings,
                meshcore_client=self.client,
            )
            self.assertEqual(self.client.last_channel_message["text"], "SMS pat: Second message")

    def test_inbound_sms_sink_submission_is_stored_and_written_to_local_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.delivery_store.upsert_sink(sink_id="school-weather", display_name="School WX")
            app.delivery_store.upsert_sink_endpoint(
                sink_id="school-weather",
                adapter_type="local-file",
                config={"path": "sinks/school-weather.jsonl"},
                sink_endpoint_id="school-weather-local",
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550177",
                    "body": "#School-Weather 21.4C 68% 1012hPa",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-13T12:05:00+00:00",
                },
                settings=settings,
                meshcore_client=None,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["target"], {"target_type": "sink", "target_id": "school-weather"})
            delivery = payload["meshcore_delivery"]
            self.assertEqual(delivery["mode"], "sink")
            self.assertTrue(delivery["accepted"])
            self.assertEqual(delivery["status"], "delivered")
            self.assertEqual(delivery["queued_count"], 1)
            self.assertFalse(payload["reply_queued"])

            sink_file = settings.delivery_db_path.parent / "sinks" / "school-weather.jsonl"
            self.assertTrue(sink_file.exists())
            record = json.loads(sink_file.read_text(encoding="utf-8").strip().splitlines()[-1])
            self.assertEqual(record["body"], "21.4C 68% 1012hPa")
            self.assertRegex(record["sender_user_id"], r"^user-[a-f0-9]{12}$")

            sinks_payload = build_workbench_sinks_payload(app)
            self.assertEqual(sinks_payload["sink_count"], 1)
            sink_row = sinks_payload["sinks"][0]
            self.assertEqual(sink_row["sink_id"], "school-weather")
            self.assertEqual(sink_row["pending_count"], 0)
            self.assertEqual(sink_row["endpoints"][0]["config_summary"], "sinks/school-weather.jsonl")
            self.assertTrue(sink_row["endpoints"][0]["adapter_available"])
            self.assertNotIn("config", sink_row["endpoints"][0])

            queue_payload = build_workbench_delivery_queue_payload(app)
            self.assertEqual(queue_payload["route_counts"].get("sink"), 1)
            self.assertTrue(all("plaintext_body" not in row for row in queue_payload["messages"]))

    def test_inbound_sms_to_unknown_sink_is_rejected_without_queueing(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550177",
                    "body": "#nowhere 21.4C",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-13T12:06:00+00:00",
                },
                settings=settings,
                meshcore_client=None,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["meshcore_delivery"]["status"], "unknown_sink")
            self.assertFalse(payload["meshcore_delivery"]["accepted"])
            self.assertEqual(app.delivery_store.list_pending_messages(), [])

            status, dispatched = dispatch_sink_queued_messages_payload(app, limit=5)
            self.assertEqual(status.value, 200)
            self.assertEqual(dispatched["dispatched_count"], 0)

    def test_inbound_sms_to_unknown_user_replies_unknown_user_and_does_not_queue(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550188",
                    "body": "@nobody-here Are you there?",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-13T12:07:00+00:00",
                },
                settings=settings,
                meshcore_client=None,
            )

            self.assertEqual(status.value, 200)
            delivery = payload["meshcore_delivery"]
            self.assertEqual(delivery["status"], "unknown_target")
            self.assertFalse(delivery["accepted"])
            self.assertEqual(delivery["target_id"], "nobody-here")
            self.assertTrue(payload["reply_queued"])
            self.assertEqual(payload["registration_reply"], "unknown user")

            pending = [
                row for row in app.delivery_store.list_pending_messages()
                if row.get("target_user_id") == "nobody-here"
            ]
            self.assertEqual(pending, [])
            gateway = app.gateway_registry.find_provider_for_network("sms")
            outbound = gateway.list_outbound_messages(limit=5)
            self.assertEqual(outbound[0]["to_number"], "+15550188")
            self.assertEqual(outbound[0]["body"], "unknown user")
            statuses = [event.get("status") for event in app.message_events.list_events()]
            self.assertIn("unknown_target", statuses)

    def test_device_payload_projects_device_query_and_stats(self) -> None:
        device = build_device_payload(self.settings, self.client)

        self.assertEqual(device["device_query"]["model"], "Pi Probe Board")
        self.assertEqual(device["battery"]["battery_mv"], 4100)
        self.assertEqual(device["stats"]["packets"]["recv_errors"], 1)

    def test_directory_payload_is_empty_and_not_preview_when_db_has_no_users(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "windows-authority",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "authority" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )

            payload = build_directory_payload(settings)

            self.assertFalse(payload["is_preview"])
            self.assertEqual(payload["source"], "directory-db")
            self.assertEqual(payload["station_role"], "authority")
            self.assertEqual(payload["user_count"], 0)
            self.assertEqual(payload["users"], [])

    def test_directory_payload_reads_saved_authority_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "windows-authority",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "authority" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )

            status, response = save_directory_user_payload(
                settings,
                {
                    "user": {"display_name": "Jane Example", "status": "active", "home_region": "East Sector", "retention_class": "TRUSTED_STATION", "promotion_status": "PROMOTED"},
                    "identity": {"display_name": "jane-1234", "identity_type": "USER", "messaging_mode": "BOTH", "policy_id": "standard-member"},
                    "profile": {
                        "personal": {"given_name": "Jane", "family_name": "Example", "email": "jane@example.org", "member_status": "Member"},
                        "identity": {"routing_notes": "direct allowed"},
                        "notes": {"operator_notes": "certified medic"},
                    },
                    "endpoints": [{"network": "MESHCORE", "native_address": "abcdef123456", "enabled": True, "verified": True, "priority": 100, "trust_level": "verified", "replication_class": "TRUSTED_STATION"}],
                },
            )

            self.assertEqual(status.value, 200)
            payload = build_directory_payload(settings)

            self.assertFalse(payload["is_preview"])
            self.assertEqual(payload["user_count"], 1)
            self.assertEqual(payload["users"][0]["identity"]["display_name"], "jane-1234")
            self.assertEqual(payload["users"][0]["profile"]["personal"]["email"], "jane@example.org")

    def test_directory_payload_preserves_multiple_saved_endpoints(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "windows-authority",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "authority" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )

            status, response = save_directory_user_payload(
                settings,
                {
                    "user": {"display_name": "Multi Endpoint User", "status": "active", "home_region": "East Sector", "retention_class": "TRUSTED_STATION", "promotion_status": "PROMOTED"},
                    "identity": {"display_name": "multi-endpoint-user", "identity_type": "USER", "messaging_mode": "BOTH", "policy_id": "standard-member"},
                    "profile": {
                        "personal": {"given_name": "Multi", "family_name": "Endpoint", "email": "multi@example.org", "member_status": "Member"},
                        "identity": {"routing_notes": "multi gateway user"},
                        "notes": {"operator_notes": "validation record"},
                    },
                    "endpoints": [
                        {"network": "MESHCORE", "native_address": "meshcore-public-key", "enabled": True, "verified": True, "priority": 100, "trust_level": "verified", "replication_class": "TRUSTED_STATION"},
                        {"network": "SMS", "native_address": "+441234567890", "enabled": True, "verified": False, "priority": 80, "trust_level": "draft", "replication_class": "TRUSTED_STATION"},
                        {"network": "EMAIL", "native_address": "multi@example.org", "enabled": True, "verified": False, "priority": 60, "trust_level": "draft", "replication_class": "FIELD_OPERATIONAL"},
                    ],
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(len(response["entry"]["endpoints"]), 3)

            payload = build_directory_payload(settings)
            self.assertEqual(payload["user_count"], 1)
            # Endpoints are returned in priority-descending order.
            self.assertEqual(
                [endpoint["network"] for endpoint in payload["users"][0]["endpoints"]],
                ["MESHCORE", "SMS", "EMAIL"],
            )

    def test_send_directed_private_message_payload_uses_runtime_client(self) -> None:
        status, payload = send_directed_private_message_payload(
            self.client,
            {"recipient_public_key": "a" * 64, "text": "hello from workbench"},
        )

        self.assertEqual(status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["text"], "hello from workbench")

    def test_set_node_tx_power_payload_uses_runtime_client(self) -> None:
        status, payload = set_node_tx_power_payload(self.client, {"tx_power_dbm": 17})

        self.assertEqual(status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["payload"]["tx_power_dbm"], 17)

    def test_android_sms_link_status_payload_projects_gateway_status(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "windows-authority",
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "android-sms-1",
                    "BASESTATION_SMS_DISPLAY_NAME": "Android SMS Gateway",
                    "BASESTATION_SMS_ANDROID_AUTH_TOKEN": "secret-token",
                },
            )
            app = create_app(settings=settings)
            gateway = app.gateway_registry.find_provider_for_network("sms")

            payload = build_android_sms_link_status_payload(settings, gateway, observed_base_url="http://127.0.0.1:8098")

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["gateway_id"], "android-sms-1")
            self.assertEqual(payload["basestation"]["gateway_id"], "windows-authority")

    def test_android_sms_inbound_payload_routes_message_and_creates_provisional_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550100",
                    "body": "@alex-2000 Need pickup",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-02T12:00:00+00:00",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["action"], "route")
            self.assertEqual(payload["sender_resolution"], "provisional")
            self.assertIsNotNone(app.directory_store.find_user_by_endpoint(network="sms", native_address="+15550100"))

    def test_android_sms_presence_only_registration_queues_unknown_user_reply_without_provisional_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                },
            )
            app = create_app(settings=settings)

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "body": "register",
                    "transport": "sms",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertTrue(payload["presence_only"])
            self.assertEqual(payload["action"], "register")
            self.assertEqual(payload["sender_resolution"], "unknown_user")
            self.assertEqual(payload["registration_reply"], "unknown user")
            self.assertIsNone(app.directory_store.find_user_by_endpoint(network="sms", native_address="+15550101"))

            claim_status, claim_payload = claim_android_sms_outbound_payload(app, limit=10)
            self.assertEqual(claim_status.value, 200)
            self.assertEqual(len(claim_payload["messages"]), 1)
            self.assertEqual(claim_payload["messages"][0]["body"], "unknown user")

    def test_android_sms_presence_only_registration_queues_registered_reply_for_known_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "field-tester",
                        "display_name": "Field Tester",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "field-tester",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550101",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "body": "register",
                    "transport": "sms",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["action"], "register")
            self.assertEqual(payload["sender_resolution"], "authority_copy")
            self.assertEqual(payload["registration_reply"], "registered")
            self.assertIsNotNone(app.directory_store.find_user_by_endpoint(network="sms", native_address="+15550101"))

            claim_status, claim_payload = claim_android_sms_outbound_payload(app, limit=10)
            self.assertEqual(claim_status.value, 200)
            self.assertEqual(len(claim_payload["messages"]), 1)
            self.assertEqual(claim_payload["messages"][0]["body"], "registered")

    def test_android_sms_notification_duplicate_is_suppressed_after_sms_copy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            first_status, first_payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550101",
                    "body": "@alex-2000 Need pickup",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-02T12:00:00+00:00",
                },
            )
            second_status, second_payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_name": "Alex Example",
                    "body": "@alex-2000 Need pickup",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-02T12:00:20+00:00",
                },
            )

            self.assertEqual(first_status.value, 200)
            self.assertFalse(first_payload["duplicate_suppressed"])
            self.assertEqual(second_status.value, 200)
            self.assertTrue(second_payload["duplicate_suppressed"])

            sms_gateway_status, sms_gateway_payload = build_sms_gateway_payload(app)
            self.assertEqual(sms_gateway_status.value, 200)
            self.assertEqual(sms_gateway_payload["inbound_count"], 1)
            self.assertEqual(sms_gateway_payload["inbound_events"][0]["network"], "sms")

    def test_android_rcs_inbound_matches_sms_authority_endpoint_when_number_is_present(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "steve-burner",
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "steve-burner",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550999",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550999",
                    "from_name": "Steve Burner",
                    "body": "@alex-2000 Need pickup",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-05T18:00:00+00:00",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["sender_resolution"], "authority_copy")
            self.assertIsNotNone(app.directory_store.find_user_by_endpoint(network="sms", native_address="+15550999"))
            gateway = app.gateway_registry.find_provider_for_network("sms")
            self.assertEqual(gateway.list_inbound_events(limit=1)[0]["sender_user_id"], "steve-burner")

            _, registration = receive_android_sms_inbound_payload(
                app, {"from_number": "+15550999", "body": "register", "transport": "rcs",
                      "observed_at_utc": "2026-09-05T18:02:00+00:00"},
            )
            self.assertEqual(registration["registration_reply"], "registered")
            self.assertTrue(registration["reply_queued"])
            self.assertEqual(gateway.list_outbound_messages(limit=1)[0]["to_number"], "+15550999")

    def test_android_rcs_name_only_inbound_matches_authority_rcs_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "user_id": "steve-burner",
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "steve-burner",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {
                            "rcs_name": "Steve Burner",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550999",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_name": "Steve Burner",
                    "body": "@alex-2000 Need pickup",
                    "transport": "rcs",
                    "observed_at_utc": "2026-09-05T18:10:00+00:00",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["sender_resolution"], "authority_rcs_name_copy")
            gateway = app.gateway_registry.find_provider_for_network("sms")
            self.assertEqual(gateway.list_inbound_events(limit=1)[0]["sender_user_id"], "steve-burner")

            _, registration = receive_android_sms_inbound_payload(
                app, {"from_name": "Steve Burner", "body": "register", "transport": "rcs",
                      "observed_at_utc": "2026-09-05T18:12:00+00:00"},
            )
            self.assertEqual(registration["registration_reply"], "registered")
            self.assertEqual(gateway.list_outbound_messages(limit=1)[0]["to_number"], "+15550999")

            _, unknown = receive_android_sms_inbound_payload(
                app, {"from_name": "Unknown RCS", "body": "register", "transport": "rcs",
                      "observed_at_utc": "2026-09-05T18:14:00+00:00"},
            )
            self.assertFalse(unknown["reply_queued"])

    def test_group_only_sms_sender_gets_direct_rejection(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = BasestationSettings.from_env(project_root=Path(temp_dir), env={"BASESTATION_SMS_ENABLED": "1"})
            app = create_app(settings=settings)
            app.authority_directory_store.save_directory_user({
                "user": {"user_id": "group-user", "display_name": "Group user", "status": "active"},
                "identity": {"display_name": "group-user", "messaging_mode": "GROUP"},
                "endpoints": [{"network": "SMS", "native_address": "+15550199", "enabled": True}],
            })

            _, result = receive_android_sms_inbound_payload(
                app, {"from_number": "+15550199", "body": "@burner hello", "transport": "sms"},
                settings=settings, meshcore_client=self.client,
            )

            self.assertEqual(result["reason"], "sender_mode_not_allowed")
            self.assertIsNone(result["meshcore_delivery"])
            self.assertEqual(result["registration_reply"], "message not allowed")

    def test_android_sms_outbound_completion_updates_queued_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "android-sms-1",
                },
            )
            app = create_app(settings=settings)

            result = app.delivery_orchestrator.send_directed_private(
                network="sms",
                request=DirectedPrivateDeliveryRequest(
                    delivery_id="deliv-sms-1",
                    destination={"native_address": "+15550102"},
                    payload={"text": "Test outbound SMS"},
                ),
            )
            self.assertTrue(result.accepted)

            claim_status, claim_payload = claim_android_sms_outbound_payload(app, limit=10)
            self.assertEqual(claim_status.value, 200)
            self.assertEqual(len(claim_payload["messages"]), 1)
            message_id = claim_payload["messages"][0]["message_id"]

            completion_status, completion_payload = complete_android_sms_outbound_payload(
                app,
                message_id=message_id,
                request_payload={"ok": True, "provider_message_id": "android-123", "detail": "Sent through Android SmsManager"},
            )

            self.assertEqual(completion_status.value, 200)
            self.assertTrue(completion_payload["ok"])
            self.assertEqual(completion_payload["completion"]["state"], "delivered")

    def test_dispatch_meshcore_queued_messages_payload_delivers_shared_queue_work(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "0",
                    "BASESTATION_MESHCORE_BASE_URL": "",
                },
            )
            app = create_app(settings=settings)
            client = _FakeMeshCoreRuntimeClient()

            app.delivery_queue.enqueue_route_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                route_name="meshcore",
                endpoint_network="meshcore",
                endpoint_address="PUBKEY-ALICE",
                plaintext_body="queued meshcore message",
                accepted_station_id=settings.station_id,
                owning_station_id=settings.station_id,
            )
            gateway = app.gateway_registry.find_provider_for_network("meshcore")
            gateway.runtime_client = client

            status, payload = dispatch_meshcore_queued_messages_payload(app, client, limit=10)

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["dispatched_count"], 1)
            self.assertEqual(payload["results"][0]["completion"]["state"], "delivered")

    def test_workbench_delivery_queue_payload_redacts_message_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "0",
                },
            )
            app = create_app(settings=settings)

            app.delivery_store.enqueue_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                target_endpoint_network="meshcore",
                target_endpoint_address="PUBKEY-N2DH",
                conversation_scope="single",
                message_class="message",
                plaintext_body="Need update",
                accepted_station_id=settings.station_id,
                owning_station_id=settings.station_id,
                selected_route="meshcore",
            )

            payload = build_workbench_delivery_queue_payload(app)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["message_count"], 1)
            self.assertEqual(payload["ready_message_count"], 1)
            self.assertEqual(payload["route_counts"]["meshcore"], 1)
            self.assertNotIn("plaintext_body", payload["messages"][0])
            self.assertNotIn("target_endpoint_address", payload["messages"][0])
            self.assertTrue(payload["messages"][0]["has_target_endpoint"])

    def test_send_sms_gateway_message_payload_projects_shared_queue_record(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                },
            )
            app = create_app(settings=settings)

            status, payload = send_sms_gateway_message_payload(
                app,
                {"to_number": "+15550124", "body": "Outbound test"},
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["payload"]["to_number"], "+15550124")
            self.assertEqual(payload["payload"]["body"], "Outbound test")
            self.assertEqual(payload["payload"]["state"], "waiting_for_path")

    def test_sms_gateway_payload_surfaces_inbound_and_outbound_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                },
            )
            app = create_app(settings=settings)

            receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550123",
                    "body": "@alex-2000 Need pickup",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-02T12:00:00+00:00",
                },
            )
            app.delivery_orchestrator.send_directed_private(
                network="sms",
                request=DirectedPrivateDeliveryRequest(
                    delivery_id="deliv-sms-2",
                    destination={"native_address": "+15550124"},
                    payload={"text": "Outbound test"},
                ),
            )

            status, payload = build_sms_gateway_payload(app)

            self.assertEqual(status.value, 200)
            self.assertTrue(payload["available"])
            self.assertEqual(payload["gateway"]["gateway_id"], "EHN-NNJ")
            self.assertEqual(payload["inbound_count"], 1)
            self.assertEqual(payload["outbound_count"], 1)
            self.assertEqual(payload["inbound_events"][0]["from_number"], "+15550123")
            self.assertEqual(payload["outbound_messages"][0]["to_number"], "+15550124")
            self.assertEqual(payload["outbound_messages"][0]["content_visibility"], "redacted")
            self.assertNotIn("body", payload["outbound_messages"][0])

    def test_register_reply_uses_preferred_language_template(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "android-sms-1",
                },
            )
            app = create_app(settings=settings)
            app.reply_templates.delivery_store.save_reply_template(
                template_key="registered",
                language_code="es",
                template_text="registrado",
            )
            app.authority_directory_store.save_directory_user(
                {
                    "user": {
                        "display_name": "Spanish User",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "spanish-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "personal": {
                            "preferred_language": "es",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550188",
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {
                    "from_number": "+15550188",
                    "body": "register",
                    "transport": "sms",
                    "observed_at_utc": "2026-09-06T12:00:00+00:00",
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["registration_reply"], "registrado")


if __name__ == "__main__":
    unittest.main()
