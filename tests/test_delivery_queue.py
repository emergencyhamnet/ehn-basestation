from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.directory.delivery_store import DeliveryStore


class DeliveryQueueServiceTests(unittest.TestCase):
    def test_claim_and_complete_route_message_hides_claim_token_from_adapter(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "delivery" / "ehn-delivery.db")
            store.initialize()
            service = DeliveryQueueService(delivery_store=store)

            record = service.enqueue_route_message(
                sender_user_id="system",
                target_user_id="member-1",
                route_name="sms",
                endpoint_network="sms",
                endpoint_address="+15550001",
                plaintext_body="hello",
                accepted_station_id="station-a",
                owning_station_id="station-a",
                message_class="system_reply",
                created_by_event_id="event-1",
            )

            claimed = service.claim_route_messages(route_name="sms", station_id="station-a", limit=5)

            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].message_id, record.message_id)
            self.assertEqual(claimed[0].endpoint_address, "+15550001")
            self.assertTrue(claimed[0].claim_token)

            completion = service.complete_route_message(
                route_name="sms",
                station_id="station-a",
                message_id=record.message_id,
                ok=True,
                provider_message_id="provider-1",
                detail="sent",
            )

            self.assertIsNotNone(completion)
            assert completion is not None
            self.assertEqual(completion["state"], "delivered")

    def test_failed_route_message_returns_to_waiting_without_adapter_store_access(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "delivery" / "ehn-delivery.db")
            store.initialize()
            service = DeliveryQueueService(delivery_store=store)

            record = service.enqueue_route_message(
                sender_user_id="system",
                target_user_id="member-1",
                route_name="sms",
                endpoint_network="sms",
                endpoint_address="+15550001",
                plaintext_body="hello",
                accepted_station_id="station-a",
                owning_station_id="station-a",
            )
            service.claim_route_messages(route_name="sms", station_id="station-a", limit=5)

            completion = service.complete_route_message(
                route_name="sms",
                station_id="station-a",
                message_id=record.message_id,
                ok=False,
                detail="temporary failure",
            )

            self.assertIsNotNone(completion)
            assert completion is not None
            self.assertEqual(completion["state"], "retry_scheduled")
            self.assertTrue(completion["next_attempt_after"])
            current = store.get_pending_message(record.message_id)
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.delivery_state, "waiting_for_path")
            self.assertEqual(current.last_error_code, "retryable_failure")
            self.assertTrue(current.next_attempt_after)


if __name__ == "__main__":
    unittest.main()