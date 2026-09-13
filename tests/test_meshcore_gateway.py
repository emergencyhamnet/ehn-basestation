from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.core.contracts import DirectedPrivateDeliveryRequest
from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.core.events import MessageEventStore
from ehn_basestation.core.orchestrator import DeliveryOrchestrator
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.gateways.meshcore import MeshCoreGatewayAdapter
from ehn_basestation.gateways.registry import GatewayRegistry


class FakeMeshCoreRuntimeClient:
    def __init__(self) -> None:
        self._snapshot = {
            "connected": True,
            "status": "connected",
            "gateway_id": "meshcore-gateway-1",
            "display_name": "MeshCore Gateway",
            "region": "EU868",
            "base_url": "http://127.0.0.1:8088",
            "identity": {
                "local_id": "LOCAL-1",
                "public_key": "PUBKEY-SELF",
                "display_name": "EHN Pi Node",
            },
            "runtime_status": {
                "transport": "meshcore-runtime",
                "interface_mode": "app",
                "helper_tx_allowed": False,
            },
        }
        self._channels = [
            {"index": 0, "name": "Primary Public", "browser_send_allowed": True},
        ]
        self._observed_nodes = [
            {
                "node_id": "node-123",
                "public_key": "PUBKEY-ALICE",
                "contact_name": "Alice",
                "short_name": "Alice",
                "long_name": "Alice Field",
                "presence_state": "PRIVATE_ACTIVE",
                "last_channel_name": "direct",
                "last_seen_at": "2026-08-21T10:00:00+00:00",
                "identity": {"identity_id": "ID-ALICE", "short_name": "Alice"},
            }
        ]
        self.last_send_request: dict | None = None

    @property
    def gateway_id(self) -> str:
        return str(self._snapshot["gateway_id"])

    def get_snapshot(self) -> dict:
        return dict(self._snapshot)

    def list_channels(self) -> list[dict]:
        return [dict(item) for item in self._channels]

    def list_observed_nodes(self) -> list[dict]:
        return [dict(item) for item in self._observed_nodes]

    def list_gateway_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict]:
        return [{"event_id": "evt-1", "message_class": "directed_private"}][:limit]

    def list_gateway_outbound_status(self, *, limit: int = 50, state: str | None = None) -> list[dict]:
        rows = [{"request_id": "req-1", "state": "sent", "transport_message_id": "msg-1"}]
        if state:
            rows = [row for row in rows if row["state"] == state]
        return rows[:limit]

    def send_directed_private_message(
        self,
        *,
        recipient_public_key: str,
        text: str,
        sender_identity: dict | None = None,
        destination_identity: dict | None = None,
        delivery_id: str | None = None,
        reply_context: dict | None = None,
    ) -> tuple[bool, str, dict | None]:
        self.last_send_request = {
            "recipient_public_key": recipient_public_key,
            "text": text,
            "sender_identity": dict(sender_identity or {}),
            "destination_identity": dict(destination_identity or {}),
            "delivery_id": delivery_id,
            "reply_context": dict(reply_context or {}),
        }
        return True, "accepted", {
            "status": "sent",
            "message_id": "mesh-msg-1",
            "observed_at": "2026-08-21T10:01:00+00:00",
        }


class MeshCoreGatewayAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = FakeMeshCoreRuntimeClient()
        self.adapter = MeshCoreGatewayAdapter(runtime_client=self.runtime)

    def test_status_projects_runtime_snapshot(self) -> None:
        status = self.adapter.get_status()

        self.assertEqual(status["gateway_id"], "meshcore-gateway-1")
        self.assertEqual(status["transport"], "meshcore-runtime")
        self.assertTrue(status["connected"])
        self.assertEqual(status["management"]["mode"], "app")
        self.assertEqual(status["details"]["observed_nodes"], 1)

    def test_observed_candidates_include_public_key_and_identity(self) -> None:
        candidates = self.adapter.list_observed_candidates()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["network"], "meshcore")
        self.assertEqual(candidates[0]["native_identity"]["public_key"], "PUBKEY-ALICE")
        self.assertEqual(candidates[0]["identity"]["short_name"], "Alice")

    def test_directed_private_send_uses_runtime_surface(self) -> None:
        result = self.adapter.send_directed_private(
            DirectedPrivateDeliveryRequest(
                delivery_id="deliv-1",
                destination={"public_key": "PUBKEY-ALICE", "native_address": "PUBKEY-ALICE"},
                payload={"text": "hello over meshcore"},
                source_identity={"identity_id": "ID-SRC", "short_name": "Base"},
                metadata={"correlation_id": "corr-1"},
            )
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.transport_message_id, "mesh-msg-1")
        self.assertIsNotNone(self.runtime.last_send_request)
        self.assertEqual(self.runtime.last_send_request["recipient_public_key"], "PUBKEY-ALICE")
        self.assertEqual(self.runtime.last_send_request["text"], "hello over meshcore")
        self.assertEqual(self.runtime.last_send_request["reply_context"]["correlation_id"], "corr-1")

    def test_dispatch_queued_messages_uses_delivery_queue_service(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "delivery.db")
            store.initialize()
            queue = DeliveryQueueService(delivery_store=store)
            queue.enqueue_route_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                route_name="meshcore",
                endpoint_network="meshcore",
                endpoint_address="PUBKEY-ALICE",
                plaintext_body="queued hello",
                accepted_station_id="field-station",
                owning_station_id="field-station",
            )
            adapter = MeshCoreGatewayAdapter(runtime_client=self.runtime, delivery_queue=queue, station_id="field-station")

            results = adapter.dispatch_queued_messages(limit=5)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["dispatch_ok"])
            self.assertEqual(results[0]["completion"]["state"], "delivered")
            self.assertEqual(self.runtime.last_send_request["delivery_id"], results[0]["message_id"])


class MeshCoreOrchestratorTests(unittest.TestCase):
    def test_orchestrator_dispatches_through_meshcore_adapter_and_records_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = FakeMeshCoreRuntimeClient()
            adapter = MeshCoreGatewayAdapter(runtime_client=runtime)
            registry = GatewayRegistry()
            registry.register(adapter)
            events = MessageEventStore()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            orchestrator = DeliveryOrchestrator(
                gateway_registry=registry,
                message_events=events,
                delivery_store=delivery_store,
                station_id="field-station",
            )

            result = orchestrator.send_directed_private(
                network="meshcore",
                request=DirectedPrivateDeliveryRequest(
                    delivery_id="deliv-2",
                    destination={"public_key": "PUBKEY-ALICE"},
                    payload={"text": "test route"},
                ),
            )

            self.assertTrue(result.accepted)
            self.assertEqual(result.gateway_id, "meshcore-gateway-1")
            recorded_events = events.list_events()
            self.assertEqual(len(recorded_events), 1)
            self.assertEqual(recorded_events[0]["status"], "sent")
            self.assertEqual(recorded_events[0]["gateway_id"], "meshcore-gateway-1")

    def test_orchestrator_queue_can_be_dispatched_through_meshcore_worker(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = FakeMeshCoreRuntimeClient()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            queue = DeliveryQueueService(delivery_store=delivery_store)
            adapter = MeshCoreGatewayAdapter(runtime_client=runtime, delivery_queue=queue, station_id="field-station")
            registry = GatewayRegistry()
            registry.register(adapter)
            events = MessageEventStore()
            DeliveryOrchestrator(
                gateway_registry=registry,
                message_events=events,
                delivery_store=delivery_store,
                station_id="field-station",
            )

            queued = delivery_store.enqueue_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                target_endpoint_network="meshcore",
                target_endpoint_address="PUBKEY-ALICE",
                conversation_scope="single",
                message_class="message",
                plaintext_body="delayed test",
                accepted_station_id="field-station",
                owning_station_id="field-station",
                selected_route="meshcore",
                created_by_event_id="evt-queued",
            )

            results = adapter.dispatch_queued_messages(limit=5)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["message_id"], queued.message_id)
            current = delivery_store.get_pending_message(queued.message_id)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.delivery_state, "delivered")


if __name__ == "__main__":
    unittest.main()