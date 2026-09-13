from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import urllib.error

from ehn_basestation.core.delivery_queue import DeliveryQueueService
from ehn_basestation.core.events import MessageEventStore
from ehn_basestation.core.sink_delivery import SinkDeliveryService, sink_target_id
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.sinks import (
    HttpSinkAdapter,
    LocalFileSinkAdapter,
    SinkDeliveryItem,
    default_sink_adapters,
)


def _make_service(root: Path, *, adapters=None) -> tuple[DeliveryStore, SinkDeliveryService, MessageEventStore]:
    store = DeliveryStore(root / "delivery" / "ehn-delivery.db")
    store.initialize()
    events = MessageEventStore()
    service = SinkDeliveryService(
        delivery_store=store,
        delivery_queue=DeliveryQueueService(delivery_store=store),
        message_events=events,
        station_id="station-a",
        adapters=adapters if adapters is not None else default_sink_adapters(base_dir=root / "delivery"),
    )
    return store, service, events


class SinkStoreTests(unittest.TestCase):
    def test_upsert_and_list_sinks_with_endpoints(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "ehn-delivery.db")
            store.initialize()

            sink = store.upsert_sink(sink_id="School-Weather", display_name="School WX", max_body_chars=200)
            endpoint = store.upsert_sink_endpoint(
                sink_id="school-weather",
                adapter_type="local-file",
                config={"path": "sinks/wx.jsonl"},
                sink_endpoint_id="wx-local",
            )
            store.upsert_sink_endpoint(sink_id="school-weather", adapter_type="http", config={"url": "http://x"}, enabled=False)

            self.assertEqual(sink.sink_id, "school-weather")
            self.assertEqual(store.get_sink("SCHOOL-WEATHER").max_body_chars, 200)
            self.assertEqual([item.sink_id for item in store.list_sinks()], ["school-weather"])
            self.assertEqual(len(store.list_sink_endpoints("school-weather")), 2)
            self.assertEqual([item.sink_endpoint_id for item in store.list_sink_endpoints("school-weather", enabled_only=True)], ["wx-local"])
            self.assertEqual(store.get_sink_endpoint(endpoint.sink_endpoint_id).config, {"path": "sinks/wx.jsonl"})

            store.mark_sink_endpoint_delivered("wx-local", delivered_at="2026-09-13T12:00:00+00:00")
            self.assertEqual(store.get_sink_endpoint("wx-local").last_delivered_at, "2026-09-13T12:00:00+00:00")

    def test_upsert_endpoint_is_idempotent_by_id(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DeliveryStore(Path(temp_dir) / "ehn-delivery.db")
            store.initialize()
            store.upsert_sink(sink_id="s1")
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="local-file", config={"path": "a"}, sink_endpoint_id="ep")
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="local-file", config={"path": "b"}, sink_endpoint_id="ep")

            endpoints = store.list_sink_endpoints("s1")
            self.assertEqual(len(endpoints), 1)
            self.assertEqual(endpoints[0].config["path"], "b")


class SinkDeliveryServiceTests(unittest.TestCase):
    def test_submission_is_stored_then_delivered_to_local_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, service, events = _make_service(root)
            store.upsert_sink(sink_id="school-weather", accepted_actor_classes=("device", "person"))
            store.upsert_sink_endpoint(sink_id="school-weather", adapter_type="local-file", config={"path": "sinks/wx.jsonl"})

            result = service.submit(
                sink_id="school-weather",
                body="21.4C 68% 1012hPa",
                sender_user_id="device-wx-1",
                actor_class="device",
                content_class="telemetry",
                source_network="sms",
                source_gateway_id="EHN-NNJ",
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "delivered")
            self.assertEqual(len(result.queued_message_ids), 1)
            pending = store.get_pending_message(result.queued_message_ids[0])
            self.assertEqual(pending.delivery_state, "delivered")
            self.assertEqual(pending.target_user_id, sink_target_id("school-weather"))
            self.assertEqual(pending.selected_route, "sink")
            self.assertEqual(pending.conversation_scope, "sink")

            lines = (root / "delivery" / "sinks" / "wx.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["sink_id"], "school-weather")
            self.assertEqual(record["body"], "21.4C 68% 1012hPa")
            self.assertEqual(record["sender_user_id"], "device-wx-1")
            self.assertEqual(record["content_class"], "telemetry")

            statuses = [event["status"] for event in events.list_events()]
            self.assertIn("accepted_and_stored", statuses)

    def test_submission_fans_out_to_every_enabled_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, service, _ = _make_service(root)
            store.upsert_sink(sink_id="s1")
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="local-file", config={"path": "a.jsonl"})
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="local-file", config={"path": "b.jsonl"})
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="local-file", config={"path": "c.jsonl"}, enabled=False)

            result = service.submit(
                sink_id="s1", body="x", sender_user_id="u", actor_class="person", content_class="message",
                source_network="sms", source_gateway_id="EHN-NNJ",
            )

            self.assertEqual(len(result.queued_message_ids), 2)
            self.assertTrue((root / "delivery" / "a.jsonl").exists())
            self.assertTrue((root / "delivery" / "b.jsonl").exists())
            self.assertFalse((root / "delivery" / "c.jsonl").exists())

    def test_unknown_disabled_and_policy_rejections(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, service, events = _make_service(root)
            store.upsert_sink(sink_id="off", enabled=False)
            store.upsert_sink(sink_id="devices-only", accepted_actor_classes=("device",), accepted_content_classes=("telemetry",), max_body_chars=5)
            store.upsert_sink_endpoint(sink_id="devices-only", adapter_type="local-file", config={"path": "d.jsonl"})
            store.upsert_sink(sink_id="empty")

            common = {"sender_user_id": "u", "source_network": "sms", "source_gateway_id": "EHN-NNJ"}
            self.assertEqual(service.submit(sink_id="nope", body="x", actor_class="device", content_class="telemetry", **common).status, "unknown_sink")
            self.assertEqual(service.submit(sink_id="off", body="x", actor_class="device", content_class="telemetry", **common).status, "sink_disabled")
            self.assertEqual(service.submit(sink_id="devices-only", body="x", actor_class="person", content_class="telemetry", **common).status, "actor_not_accepted")
            self.assertEqual(service.submit(sink_id="devices-only", body="x", actor_class="device", content_class="message", **common).status, "content_not_accepted")
            self.assertEqual(service.submit(sink_id="devices-only", body="x" * 6, actor_class="device", content_class="telemetry", **common).status, "body_too_long")
            self.assertEqual(service.submit(sink_id="empty", body="x", actor_class="device", content_class="telemetry", **common).status, "sink_has_no_endpoints")

            self.assertEqual(store.list_pending_messages(), [])
            self.assertTrue(all(event["network"] == "sink" for event in events.list_events()))

    def test_unreachable_http_endpoint_stays_queued_for_retry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def failing_opener(request, timeout):
                raise urllib.error.URLError("connection refused")

            adapters = {"http": HttpSinkAdapter(opener=failing_opener)}
            store, service, _ = _make_service(root, adapters=adapters)
            store.upsert_sink(sink_id="school")
            store.upsert_sink_endpoint(sink_id="school", adapter_type="http", config={"url": "http://collector.local/submit"})

            result = service.submit(
                sink_id="school", body="rain 3mm", sender_user_id="u", actor_class="device", content_class="telemetry",
                source_network="meshcore", source_gateway_id="EHN-NNJ",
            )

            self.assertTrue(result.accepted)
            self.assertEqual(result.status, "accepted_and_stored")
            pending = store.get_pending_message(result.queued_message_ids[0])
            self.assertEqual(pending.delivery_state, "waiting_for_path")
            self.assertIsNotNone(pending.next_attempt_after)
            self.assertEqual(pending.claim_station_id, "")

    def test_http_4xx_is_not_retried(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def rejecting_opener(request, timeout):
                raise urllib.error.HTTPError(request.full_url, 400, "bad", hdrs=None, fp=None)

            store, service, _ = _make_service(root, adapters={"http": HttpSinkAdapter(opener=rejecting_opener)})
            store.upsert_sink(sink_id="school")
            store.upsert_sink_endpoint(sink_id="school", adapter_type="http", config={"url": "http://collector.local/submit"})

            result = service.submit(
                sink_id="school", body="x", sender_user_id="u", actor_class="device", content_class="telemetry",
                source_network="sms", source_gateway_id="EHN-NNJ",
            )

            pending = store.get_pending_message(result.queued_message_ids[0])
            self.assertEqual(pending.delivery_state, "failed")

    def test_missing_adapter_fails_closed_without_retry(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, service, _ = _make_service(root, adapters={})
            store.upsert_sink(sink_id="s1")
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="email", config={"to": "x@y"})

            result = service.submit(
                sink_id="s1", body="x", sender_user_id="u", actor_class="person", content_class="message",
                source_network="sms", source_gateway_id="EHN-NNJ",
            )

            pending = store.get_pending_message(result.queued_message_ids[0])
            self.assertEqual(pending.delivery_state, "failed")

    def test_deferred_dispatch_delivers_later(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store, service, _ = _make_service(root)
            store.upsert_sink(sink_id="s1")
            store.upsert_sink_endpoint(sink_id="s1", adapter_type="local-file", config={"path": "later.jsonl"})

            result = service.submit(
                sink_id="s1", body="x", sender_user_id="u", actor_class="person", content_class="message",
                source_network="sms", source_gateway_id="EHN-NNJ", dispatch_now=False,
            )
            self.assertEqual(result.status, "accepted_and_stored")
            self.assertFalse((root / "delivery" / "later.jsonl").exists())

            dispatched = service.dispatch(limit=10)

            self.assertEqual(len(dispatched), 1)
            self.assertTrue(dispatched[0]["dispatch_ok"])
            self.assertTrue((root / "delivery" / "later.jsonl").exists())


class SinkAdapterTests(unittest.TestCase):
    def test_local_file_adapter_requires_path(self) -> None:
        from ehn_basestation.directory.delivery_store import SinkEndpointRecord

        endpoint = SinkEndpointRecord("ep", "s", "local-file", {}, True, 100, None, "now")
        outcome = LocalFileSinkAdapter().deliver(endpoint, SinkDeliveryItem("m", "s", "u", "message", "x", "now"))
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.retryable)

    def test_http_adapter_rejects_non_http_url(self) -> None:
        from ehn_basestation.directory.delivery_store import SinkEndpointRecord

        endpoint = SinkEndpointRecord("ep", "s", "http", {"url": "ftp://x"}, True, 100, None, "now")
        outcome = HttpSinkAdapter().deliver(endpoint, SinkDeliveryItem("m", "s", "u", "message", "x", "now"))
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.retryable)


if __name__ == "__main__":
    unittest.main()
