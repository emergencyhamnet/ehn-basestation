from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.directory.delivery_store import DeliveryStore


class DeliveryStoreTests(unittest.TestCase):
    def test_enqueue_and_list_pending_messages_redacts_content_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "delivery" / "ehn-delivery.db")
            store.initialize()

            record = store.enqueue_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                target_endpoint_network="sms",
                target_endpoint_address="+15550199",
                conversation_scope="single",
                message_class="message",
                plaintext_body="Need status update",
                accepted_station_id="station-a",
                owning_station_id="authority-main",
                body_summary="hidden",
            )

            rows = store.list_pending_messages()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["message_id"], record.message_id)
            self.assertEqual(rows[0]["target_endpoint_address"], "+15550199")
            self.assertNotIn("plaintext_body", rows[0])
            self.assertNotIn("body_summary", rows[0])

            full_row = store.list_pending_messages(include_content=True)[0]
            self.assertEqual(full_row["plaintext_body"], "Need status update")
            self.assertEqual(full_row["body_summary"], "hidden")

    def test_claim_release_and_complete_message_updates_lease_fields(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "delivery" / "ehn-delivery.db")
            store.initialize()

            record = store.enqueue_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                target_endpoint_network="sms",
                target_endpoint_address="+15550199",
                conversation_scope="single",
                message_class="message",
                plaintext_body="Need status update",
                accepted_station_id="station-a",
                owning_station_id="authority-main",
            )

            claimed = store.claim_message(message_id=record.message_id, station_id="station-a", claim_window_seconds=60)
            self.assertIsNotNone(claimed)
            assert claimed is not None
            self.assertEqual(claimed.delivery_state, "attempting")
            self.assertEqual(claimed.claim_station_id, "station-a")
            self.assertTrue(claimed.claim_token)

            renewed = store.renew_message_claim(
                message_id=record.message_id,
                station_id="station-a",
                claim_token=claimed.claim_token,
                claim_window_seconds=60,
            )
            self.assertIsNotNone(renewed)
            assert renewed is not None
            self.assertEqual(renewed.claim_station_id, "station-a")

            released = store.release_message_claim(
                message_id=record.message_id,
                station_id="station-a",
                claim_token=claimed.claim_token,
                route_name="meshcore",
                next_attempt_after="2026-09-06T19:00:00+00:00",
            )
            self.assertIsNotNone(released)
            assert released is not None
            self.assertEqual(released.delivery_state, "waiting_for_path")
            self.assertEqual(released.claim_station_id, "")

            reclaimed = store.claim_message(message_id=record.message_id, station_id="station-b", claim_window_seconds=60)
            self.assertIsNotNone(reclaimed)
            assert reclaimed is not None

            completed = store.complete_message(
                message_id=record.message_id,
                station_id="station-b",
                claim_token=reclaimed.claim_token,
                outcome="delivered",
                route_name="meshcore",
                gateway_request_id="gw-1",
            )
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed.delivery_state, "delivered")
            self.assertEqual(completed.claim_station_id, "")

    def test_claim_messages_for_route_uses_shared_pending_queue(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "delivery" / "ehn-delivery.db")
            store.initialize()

            first = store.enqueue_message(
                sender_user_id="system",
                target_user_id="",
                target_endpoint_network="sms",
                target_endpoint_address="+15550101",
                conversation_scope="single",
                message_class="system_reply",
                plaintext_body="registered",
                accepted_station_id="station-a",
                owning_station_id="station-a",
                selected_route="sms",
            )
            store.enqueue_message(
                sender_user_id="system",
                target_user_id="",
                target_endpoint_network="meshcore",
                target_endpoint_address="peer-1",
                conversation_scope="single",
                message_class="system_reply",
                plaintext_body="ignore",
                accepted_station_id="station-a",
                owning_station_id="station-a",
                selected_route="meshcore",
            )

            claimed = store.claim_messages_for_route(route_name="sms", station_id="station-a", limit=10)

            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].message_id, first.message_id)
            self.assertEqual(claimed[0].target_endpoint_address, "+15550101")


if __name__ == "__main__":
    unittest.main()