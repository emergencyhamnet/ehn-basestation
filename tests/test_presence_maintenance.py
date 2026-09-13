from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.app.bootstrap import create_app
from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.app.workbench_server import (
    build_workbench_delivery_queue_payload,
    receive_android_sms_inbound_payload,
    run_delivery_maintenance_payload,
)
from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.core.events import MessageEventStore
from ehn_basestation.core.presence import PresenceService, canonical_endpoint_network
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.gateways.registry import GatewayRegistry


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _user_payload(user_id: str, *, endpoints: list[dict]) -> dict:
    return {
        "user": {"user_id": user_id, "display_name": user_id.upper(), "status": "active", "home_region": "NNJ",
                 "retention_class": "TRUSTED_STATION", "promotion_status": "PROMOTED"},
        "identity": {"display_name": user_id, "identity_type": "USER", "messaging_mode": "BOTH"},
        "endpoints": endpoints,
    }


class _FakeMeshGateway:
    """Minimal gateway: reports observed candidates and records queued dispatch calls."""

    def __init__(self, delivery_queue: DeliveryQueueService, *, network: str = "meshcore") -> None:
        self.gateway_id = f"{network}-gateway-1"
        self.supported_networks = {network}
        self._network = network
        self._queue = delivery_queue
        self.candidates: list[dict] = []
        self.sent: list[dict] = []

    def get_status(self) -> dict:
        return {"gateway_id": self.gateway_id, "connected": True}

    def list_observed_candidates(self) -> list[dict]:
        return list(self.candidates)

    def dispatch_queued_messages(self, *, limit: int = 10) -> list[dict]:
        def _send(item):
            self.sent.append({"to": item.endpoint_address, "body": item.plaintext_body})
            return {"ok": True, "provider_message_id": f"tx-{len(self.sent)}"}

        return self._queue.dispatch_route_messages(
            route_name=self._network, station_id="station-a", limit=limit, dispatcher=_send
        )


class PresenceServiceTests(unittest.TestCase):
    def test_canonical_network_mapping(self) -> None:
        self.assertEqual(canonical_endpoint_network("RCS"), "sms")
        self.assertEqual(canonical_endpoint_network("meshcore-runtime"), "meshcore")
        self.assertEqual(canonical_endpoint_network("Meshtastic"), "meshtastic")

    def test_advert_from_known_endpoint_refreshes_and_dispatches_waiting_messages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DeliveryStore(root / "delivery.db")
            store.initialize()
            directory = DirectoryStore(root / "dir.db", origin_station_id="station-a")
            directory.initialize()
            directory.save_directory_user(_user_payload("alex", endpoints=[
                {"network": "MESHCORE", "native_address": "PUBKEY-ALEX", "enabled": True, "verified": True, "priority": 100,
                 "trust_level": "verified", "replication_class": "TRUSTED_STATION"},
            ]))
            queue = DeliveryQueueService(delivery_store=store)
            registry = GatewayRegistry()
            gateway = _FakeMeshGateway(queue)
            registry.register(gateway)
            events = MessageEventStore()
            presence = PresenceService(
                delivery_store=store, gateway_registry=registry, local_directory=directory,
                message_events=events, station_id="station-a",
            )
            # Message accepted earlier with no route: no endpoint address, so it cannot be claimed yet.
            record = store.enqueue_message(
                sender_user_id="n2dh", target_user_id="alex", conversation_scope="single", message_class="message",
                plaintext_body="are you there", accepted_station_id="station-a", owning_station_id="station-a",
                selected_route="meshcore",
            )
            self.assertEqual(queue.claim_route_messages(route_name="meshcore", station_id="station-a"), [])

            observation = presence.observe_endpoint(
                network="meshcore", native_address="PUBKEY-ALEX", evidence="advert", observed_at=_iso(),
            )

            self.assertIsNotNone(observation)
            self.assertEqual(observation.user_id, "alex")
            self.assertEqual(observation.refreshed_message_ids, (record.message_id,))
            self.assertEqual(gateway.sent, [{"to": "PUBKEY-ALEX", "body": "are you there"}])
            self.assertEqual(store.get_pending_message(record.message_id).delivery_state, "delivered")
            presence_row = store.get_user_presence("alex")
            self.assertEqual(presence_row.presence_evidence, "advert")
            self.assertEqual(presence_row.last_seen_via_network, "meshcore")
            self.assertIn("presence_observed", [event["event_type"] for event in events.list_events()])

    def test_unknown_endpoint_and_stale_advert_are_ignored(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DeliveryStore(root / "delivery.db")
            store.initialize()
            directory = DirectoryStore(root / "dir.db", origin_station_id="station-a")
            directory.initialize()
            directory.save_directory_user(_user_payload("alex", endpoints=[
                {"network": "MESHCORE", "native_address": "PUBKEY-ALEX", "enabled": True, "verified": True, "priority": 100,
                 "trust_level": "verified", "replication_class": "TRUSTED_STATION"},
            ]))
            presence = PresenceService(
                delivery_store=store, gateway_registry=GatewayRegistry(), local_directory=directory, station_id="station-a",
            )

            self.assertIsNone(presence.observe_endpoint(network="meshcore", native_address="PUBKEY-GHOST", evidence="advert"))
            self.assertIsNone(store.get_user_presence("alex"))

            first = presence.observe_endpoint(network="meshcore", native_address="PUBKEY-ALEX", evidence="advert", observed_at=_iso())
            stale = presence.observe_endpoint(network="meshcore", native_address="PUBKEY-ALEX", evidence="advert", observed_at=_iso(-3600))
            self.assertIsNotNone(first)
            self.assertIsNone(stale)

    def test_sweep_uses_any_gateway_that_reports_candidates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DeliveryStore(root / "delivery.db")
            store.initialize()
            directory = DirectoryStore(root / "dir.db", origin_station_id="station-a")
            directory.initialize()
            directory.save_directory_user(_user_payload("tasha", endpoints=[
                {"network": "MESHTASTIC", "native_address": "!a1b2c3d4", "enabled": True, "verified": True, "priority": 100,
                 "trust_level": "verified", "replication_class": "TRUSTED_STATION"},
            ]))
            queue = DeliveryQueueService(delivery_store=store)
            registry = GatewayRegistry()
            meshtastic = _FakeMeshGateway(queue, network="meshtastic")
            meshtastic.candidates = [{
                "network": "meshtastic", "observed_at": _iso(),
                "native_identity": {"public_key": None, "node_id": "!a1b2c3d4"},
            }]
            registry.register(meshtastic)
            presence = PresenceService(delivery_store=store, gateway_registry=registry, local_directory=directory, station_id="station-a")
            store.enqueue_message(
                sender_user_id="n2dh", target_user_id="tasha", conversation_scope="single", message_class="message",
                plaintext_body="hello tasha", accepted_station_id="station-a", owning_station_id="station-a",
                selected_route="meshtastic",
            )

            observations = presence.sweep_gateway_observations()

            self.assertEqual([item.user_id for item in observations], ["tasha"])
            self.assertEqual(meshtastic.sent, [{"to": "!a1b2c3d4", "body": "hello tasha"}])


class MaintenanceTests(unittest.TestCase):
    def _app(self, root: Path):
        settings = BasestationSettings.from_env(
            project_root=root,
            env={
                "BASESTATION_MESHCORE_ENABLED": "0",
                "BASESTATION_SMS_ENABLED": "1",
                "BASESTATION_SMS_GATEWAY_ID": "EHN-NNJ",
                "EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS": "0",
            },
        )
        return settings, create_app(settings=settings)

    def test_maintenance_expires_stale_message_and_notifies_sms_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, app = self._app(root)
            app.directory_store.save_directory_user(_user_payload("sender", endpoints=[
                {"network": "SMS", "native_address": "+15550001", "enabled": True, "verified": True, "priority": 100,
                 "trust_level": "verified", "replication_class": "TRUSTED_STATION"},
            ]))
            app.directory_store.save_directory_user(_user_payload("target", endpoints=[]))
            app.delivery_store.upsert_delivery_policy(user_id="target", preferred_routes=["meshcore"], message_expiry_seconds=60)
            record = app.delivery_store.enqueue_message(
                sender_user_id="sender", target_user_id="target", conversation_scope="single", message_class="message",
                plaintext_body="old news", accepted_station_id="s", owning_station_id="s", selected_route="meshcore",
            )

            summary = app.maintenance.run_once(now=_iso(120))

            self.assertEqual(summary.expired, 1)
            self.assertEqual(summary.expiry_notices_queued, 1)
            self.assertEqual(app.delivery_store.get_pending_message(record.message_id).delivery_state, "expired")
            self.assertEqual(app.delivery_store.get_pending_message(record.message_id).last_error_code, "ttl_exceeded")
            outbound = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=5)
            self.assertEqual(outbound[0]["to_number"], "+15550001")
            self.assertEqual(outbound[0]["body"], "delivery expired")
            self.assertIn("delivery_maintenance", [event["event_type"] for event in app.message_events.list_events()])

    def test_maintenance_does_not_expire_fresh_messages_or_claimed_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, app = self._app(root)
            fresh = app.delivery_store.enqueue_message(
                sender_user_id="a", target_user_id="b", conversation_scope="single", message_class="message",
                plaintext_body="fresh", accepted_station_id="s", owning_station_id="s", selected_route="meshcore",
            )

            summary = app.maintenance.run_once()

            self.assertEqual(summary.expired, 0)
            self.assertEqual(app.delivery_store.get_pending_message(fresh.message_id).delivery_state, "waiting_for_path")

    def test_maintenance_releases_expired_claims(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, app = self._app(root)
            record = app.delivery_store.enqueue_message(
                sender_user_id="a", target_user_id="b", target_endpoint_network="sms", target_endpoint_address="+1",
                conversation_scope="single", message_class="message", plaintext_body="x",
                accepted_station_id="s", owning_station_id="s", selected_route="sms",
            )
            claimed = app.delivery_store.claim_message(message_id=record.message_id, station_id="dead-station", claim_window_seconds=1)
            self.assertIsNotNone(claimed)

            summary = app.maintenance.run_once(now=_iso(30))

            self.assertEqual(summary.released_claims, 1)
            after = app.delivery_store.get_pending_message(record.message_id)
            self.assertEqual(after.delivery_state, "waiting_for_path")
            self.assertEqual(after.claim_station_id, "")
            self.assertEqual(after.last_error_code, "claim_expired")

    def test_maintenance_expires_after_max_attempts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, app = self._app(root)
            app.delivery_store.upsert_delivery_policy(user_id="b", preferred_routes=["sms"], max_attempts=2)
            record = app.delivery_store.enqueue_message(
                sender_user_id="a", target_user_id="b", target_endpoint_network="sms", target_endpoint_address="+1",
                conversation_scope="single", message_class="message", plaintext_body="x",
                accepted_station_id="s", owning_station_id="s", selected_route="sms",
            )
            for _ in range(2):
                claimed = app.delivery_store.claim_message(message_id=record.message_id, station_id="s")
                app.delivery_store.release_message_claim(
                    message_id=record.message_id, station_id="s", claim_token=claimed.claim_token, route_name="sms",
                )

            summary = app.maintenance.run_once()

            self.assertEqual(summary.expired, 1)
            self.assertEqual(app.delivery_store.get_pending_message(record.message_id).last_error_code, "max_attempts_exceeded")

    def test_workbench_maintenance_route_and_queue_payload_expose_last_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, app = self._app(root)

            status, payload = run_delivery_maintenance_payload(app)
            queue_payload = build_workbench_delivery_queue_payload(app)

            self.assertEqual(status.value, 200)
            self.assertTrue(payload["ok"])
            self.assertIn("ran_at", payload["maintenance"])
            self.assertEqual(queue_payload["maintenance"]["interval_seconds"], 0)
            self.assertEqual(queue_payload["maintenance"]["last_run"]["ran_at"], payload["maintenance"]["ran_at"])

    def test_sms_register_from_target_triggers_queued_sms_redelivery(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, app = self._app(root)
            app.authority_directory_store.save_directory_user(_user_payload("target", endpoints=[
                {"network": "SMS", "native_address": "+15550099", "enabled": True, "verified": True, "priority": 100,
                 "trust_level": "verified", "replication_class": "TRUSTED_STATION"},
            ]))
            # A message accepted for the target while no route was available.
            record = app.delivery_store.enqueue_message(
                sender_user_id="n2dh", target_user_id="target", conversation_scope="single", message_class="message",
                plaintext_body="call base when you can", accepted_station_id="s", owning_station_id="s", selected_route="sms",
            )

            status, payload = receive_android_sms_inbound_payload(
                app,
                {"from_number": "+15550099", "body": "register", "transport": "sms", "observed_at_utc": _iso()},
                settings=settings,
                meshcore_client=None,
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(payload["registration_reply"], "registered")
            refreshed = app.delivery_store.get_pending_message(record.message_id)
            self.assertEqual(refreshed.target_endpoint_address, "+15550099")
            self.assertEqual(refreshed.selected_route, "sms")
            # The phone claims the redelivery on its next outbound poll.
            outbound = app.gateway_registry.find_provider_for_network("sms").list_outbound_messages(limit=10)
            bodies = {item["body"] for item in outbound}
            self.assertIn("call base when you can", bodies)


if __name__ == "__main__":
    unittest.main()
