from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib import error
import warnings

from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.gateways.meshcore import HttpMeshCoreGatewayLink, MeshCoreRuntimeClient, MeshCoreRuntimeSettings


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _http_error(url: str, code: int) -> error.HTTPError:
    return error.HTTPError(url, code, "error", hdrs=None, fp=None)


class MeshCoreRuntimeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self._warning_filter = warnings.catch_warnings()
        self._warning_filter.__enter__()
        warnings.filterwarnings("ignore", category=ResourceWarning, message=r"Implicitly cleaning up <HTTPError 404: 'error'>")

    def tearDown(self) -> None:
        self._warning_filter.__exit__(None, None, None)

    def test_runtime_client_can_wrap_gateway_link(self) -> None:
        link = HttpMeshCoreGatewayLink(
            MeshCoreRuntimeSettings(
                gateway_id="meshcore-gateway-1",
                display_name="MeshCore Gateway",
                base_url="",
                contract_version="1.0",
            )
        )

        client = MeshCoreRuntimeClient(link)

        self.assertEqual(client.gateway_id, "meshcore-gateway-1")
        self.assertEqual(client.get_snapshot()["status"], "unconfigured")

    def test_runtime_client_from_settings_can_select_stream_link(self) -> None:
        client = MeshCoreRuntimeClient.from_settings(
            BasestationSettings.from_env(
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
                    "BASESTATION_MESHCORE_LINK_ENDPOINT": "127.0.0.1:9",
                    "BASESTATION_MESHCORE_BASE_URL": "",
                    "BASESTATION_MESHCORE_TIMEOUT_SECONDS": "20",
                }
            )
        )

        snapshot = client.get_snapshot()

        self.assertEqual(snapshot["status"], "unreachable")
        self.assertEqual(snapshot["runtime_status"]["link_carrier"], "tcp")
        self.assertEqual(client._link._settings.timeout_seconds, 20.0)

    def test_runtime_contract_is_preferred_when_available(self) -> None:
        settings = MeshCoreRuntimeSettings(
            gateway_id="meshcore-gateway-1",
            display_name="MeshCore Gateway",
            base_url="http://meshcore.local:8088",
            contract_version="1.0",
        )
        responses = {
            "http://meshcore.local:8088/status": {"connected": True, "status": "connected", "region": "EU868", "runtime": {"name": "meshcore", "version": "0.9", "contract_version": "1.0"}},
            "http://meshcore.local:8088/identity": {"local_id": "LOCAL-1", "public_key": "PUBSELF"},
            "http://meshcore.local:8088/neighbors": {"neighbors": [{"node_id": "node-1", "public_key": "PUB1", "last_seen_at": "2026-08-22T10:00:00+00:00"}]},
            "http://meshcore.local:8088/channels": {"channels": [{"index": 0, "name": "Primary Public", "browser_send_allowed": False}]},
            "http://meshcore.local:8088/api/inbound-events?limit=5": {"events": [{"event_id": "evt-1"}]},
            "http://meshcore.local:8088/api/outbound-requests?limit=5&state=sent": {"requests": [{"request_id": "req-1", "state": "sent"}]},
        }

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            self.assertIn(url, responses)
            return _FakeResponse(responses[url])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = MeshCoreRuntimeClient(settings)
            snapshot = client.get_snapshot()
            channels = client.list_channels()
            neighbors = client.list_observed_nodes()
            inbound = client.list_gateway_inbound_events(limit=5)
            outbound = client.list_gateway_outbound_status(limit=5, state="sent")

        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["runtime_name"], "meshcore")
        self.assertEqual(channels[0]["name"], "Primary Public")
        self.assertEqual(neighbors[0]["public_key"], "PUB1")
        self.assertEqual(inbound[0]["event_id"], "evt-1")
        self.assertEqual(outbound[0]["request_id"], "req-1")

    def test_operational_ui_fallback_is_used_when_status_is_missing(self) -> None:
        settings = MeshCoreRuntimeSettings(
            gateway_id="meshcore-gateway-1",
            display_name="MeshCore Gateway",
            base_url="http://meshcore.local:8088",
            contract_version="1.0",
        )

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            if url == "http://meshcore.local:8088/status":
                raise _http_error(url, 404)
            if url == "http://meshcore.local:8088/api/runtime":
                return _FakeResponse(
                    {
                        "interface_mode": "web",
                        "service": {"active": True},
                        "status": {"parsed": {"transport_started": True, "runtime_endpoint": "tcp://127.0.0.1:5041"}},
                        "host": {"hostname": "meshcorepi"},
                    }
                )
            if url == "http://meshcore.local:8088/api/contacts":
                return _FakeResponse(
                    {
                        "contacts": [
                            {"name": "Alice", "pub_key": "PUBKEY-ALICE", "pub_key_prefix": "PUBKEY-ALIC", "last_advert_utc": "2026-08-22T12:00:00+00:00"}
                        ]
                    }
                )
            raise AssertionError(f"Unexpected URL {url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = MeshCoreRuntimeClient(settings)
            snapshot = client.get_snapshot()
            neighbors = client.list_observed_nodes()

        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["runtime_name"], "meshcore-pi-node-ui")
        self.assertEqual(snapshot["runtime_status"]["interface_mode"], "web")
        self.assertEqual(neighbors[0]["contact_name"], "Alice")

    def test_directed_private_send_falls_back_to_operational_ui(self) -> None:
        settings = MeshCoreRuntimeSettings(
            gateway_id="meshcore-gateway-1",
            display_name="MeshCore Gateway",
            base_url="http://meshcore.local:8088",
            contract_version="1.0",
        )
        calls: list[tuple[str, str, dict | None]] = []

        def fake_urlopen(req, timeout=0):
            body = req.data.decode("utf-8") if req.data else None
            parsed_body = json.loads(body) if body else None
            calls.append((req.full_url, req.method, parsed_body))
            if req.full_url == "http://meshcore.local:8088/status":
                return _FakeResponse({"connected": True, "status": "connected", "runtime": {"contract_version": "1.0"}})
            if req.full_url == "http://meshcore.local:8088/identity":
                return _FakeResponse({"local_id": "LOCAL-1"})
            if req.full_url == "http://meshcore.local:8088/neighbors":
                return _FakeResponse({"neighbors": []})
            if req.full_url == "http://meshcore.local:8088/channels":
                return _FakeResponse({"channels": []})
            if req.full_url == "http://meshcore.local:8088/directed_private_tx":
                raise _http_error(req.full_url, 404)
            if req.full_url == "http://meshcore.local:8088/api/outbound-requests":
                return _FakeResponse({
                    "ok": True,
                    "request": {"request_id": "req-42"},
                    "status": {"state": "queued", "transport_message_id": "msg-42", "updated_at": "2026-08-22T12:10:00+00:00"},
                })
            raise AssertionError(f"Unexpected URL {req.full_url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = MeshCoreRuntimeClient(settings)
            accepted, message, payload = client.send_directed_private_message(
                recipient_public_key="PUBKEY-ALICE",
                text="hello",
                delivery_id="deliv-1",
            )

        self.assertTrue(accepted)
        self.assertIn("accepted", message.lower())
        self.assertEqual(payload["request_id"], "req-42")
        self.assertEqual(calls[-1][0], "http://meshcore.local:8088/api/outbound-requests")
        self.assertEqual(calls[-1][2]["message_class"], "directed_private")

    def test_private_channel_send_falls_back_to_operational_ui_queue(self) -> None:
        settings = MeshCoreRuntimeSettings(
            gateway_id="meshcore-gateway-1",
            display_name="MeshCore Gateway",
            base_url="http://meshcore.local:8088",
            contract_version="1.0",
        )
        calls: list[tuple[str, str, dict | None]] = []

        def fake_urlopen(req, timeout=0):
            body = req.data.decode("utf-8") if req.data else None
            parsed_body = json.loads(body) if body else None
            calls.append((req.full_url, req.method, parsed_body))
            if req.full_url == "http://meshcore.local:8088/status":
                return _FakeResponse({"connected": True, "status": "connected", "runtime": {"contract_version": "1.0"}})
            if req.full_url == "http://meshcore.local:8088/identity":
                return _FakeResponse({"local_id": "LOCAL-1"})
            if req.full_url == "http://meshcore.local:8088/neighbors":
                return _FakeResponse({"neighbors": []})
            if req.full_url == "http://meshcore.local:8088/channels":
                return _FakeResponse({"channels": []})
            if req.full_url == "http://meshcore.local:8088/public_group_tx":
                raise _http_error(req.full_url, 404)
            if req.full_url == "http://meshcore.local:8088/api/outbound-requests":
                return _FakeResponse({
                    "ok": True,
                    "request": {"request_id": "req-77"},
                    "status": {"state": "queued", "transport_message_id": "msg-77", "updated_at": "2026-08-22T12:10:00+00:00"},
                })
            raise AssertionError(f"Unexpected URL {req.full_url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = MeshCoreRuntimeClient(settings)
            accepted, message, payload = client.send_channel_message(
                channel_index=1,
                channel_name="EHN Private",
                text="hello private channel",
                delivery_id="deliv-2",
            )

        self.assertTrue(accepted)
        self.assertIn("accepted", message.lower())
        self.assertEqual(payload["request_id"], "req-77")
        self.assertEqual(calls[-1][0], "http://meshcore.local:8088/api/outbound-requests")
        self.assertEqual(calls[-1][2]["message_class"], "group_message")


if __name__ == "__main__":
    unittest.main()