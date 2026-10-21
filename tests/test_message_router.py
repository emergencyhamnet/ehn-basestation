from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.core.contracts import DeliveryResult, DirectedPrivateDeliveryRequest
from ehn_basestation.core.events import MessageEventStore
from ehn_basestation.core.message_policy import InboundTextMessage
from ehn_basestation.core.orchestrator import DeliveryOrchestrator
from ehn_basestation.directory.delivery_store import DeliveryStore
from ehn_basestation.core.router import InboundMessageRouter
from ehn_basestation.directory.directory_store import DirectoryStore
from ehn_basestation.gateways.registry import GatewayRegistry


class _FakeGateway:
    def __init__(self) -> None:
        self.sent_requests: list[DirectedPrivateDeliveryRequest] = []
        self.dispatch_limits: list[int] = []
        self._events = [
            {
                "event_id": "evt-1",
                "message_class": "channel_message",
                "public_key": "PUBKEY-ALICE",
                "text": "Local shelter ready for supplies",
                "channel_name": "EHN Private",
            }
        ]

    @property
    def gateway_id(self) -> str:
        return "EHN-NNJ"

    @property
    def supported_networks(self) -> set[str]:
        return {"meshcore"}

    def get_status(self) -> dict[str, object]:
        return {"gateway_id": self.gateway_id, "status": "connected"}

    def list_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, object]]:
        return self._events[:limit]

    def send_directed_private(self, request: DirectedPrivateDeliveryRequest) -> DeliveryResult:
        self.sent_requests.append(request)
        return DeliveryResult(
            delivery_id=request.delivery_id,
            gateway_id=self.gateway_id,
            network="meshcore",
            accepted=True,
            status="sent",
            transport_message_id="mesh-msg-1",
        )

    def dispatch_queued_messages(self, *, limit: int = 10) -> list[dict[str, object]]:
        self.dispatch_limits.append(limit)
        return []


class MessageRouterTests(unittest.TestCase):
    def test_messaging_modes_restrict_sender_and_direct_target(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            for label, address, mode in (("group", "+15550001", "GROUP"), ("direct", "+15550002", "DIRECT"), ("both", "+15550003", "BOTH")):
                store.save_directory_user({
                    "user": {"display_name": label, "status": "active"},
                    "identity": {"display_name": label, "messaging_mode": mode},
                    "endpoints": [{"network": "SMS", "native_address": address, "enabled": True}],
                })
            events = MessageEventStore()
            router = InboundMessageRouter(gateway_registry=GatewayRegistry(), message_events=events, local_directory=store)

            sender_blocked = router.ingest_inbound_text_message(InboundTextMessage("EHN-NNJ", "sms", "+15550001", "@both hello"))
            target_blocked = router.ingest_inbound_text_message(InboundTextMessage("EHN-NNJ", "sms", "+15550002", "@group hello"))
            group_blocked = router.ingest_inbound_text_message(InboundTextMessage("EHN-NNJ", "sms", "+15550002", "& hello"))
            group_allowed = router.ingest_inbound_text_message(InboundTextMessage("EHN-NNJ", "sms", "+15550001", "& hello"))

            self.assertEqual(sender_blocked.classification.reason, "sender_mode_not_allowed")
            self.assertEqual(target_blocked.classification.reason, "target_mode_not_allowed")
            self.assertEqual(group_blocked.classification.reason, "sender_mode_not_allowed")
            self.assertEqual(group_allowed.classification.action, "route")
            self.assertEqual(events.list_events()[0]["status"], "sender_mode_not_allowed")

    def test_ingest_gateway_inbound_event_creates_provisional_sender_for_unknown_mesh_user(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            local_store.initialize()
            registry = GatewayRegistry()
            registry.register(_FakeGateway())
            events = MessageEventStore()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            router = InboundMessageRouter(
                gateway_registry=registry,
                message_events=events,
                local_directory=local_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            results = router.ingest_gateway_inbound_events(network="meshcore")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].classification.conversation_scope, "group")
            self.assertEqual(results[0].sender_resolution, "provisional")
            self.assertIsNotNone(results[0].sender_entry)
            self.assertEqual(results[0].sender_entry.endpoints[0].native_address, "PUBKEY-ALICE")
            self.assertEqual(events.list_events()[0]["target_id"], "EHN-NNJ")

    def test_ingest_sms_message_copies_sender_from_authority_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            authority_store = DirectoryStore(Path(temp_dir) / "authority.db", origin_station_id="authority")
            local_store.initialize()
            authority_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            authority_store.save_directory_user(
                {
                    "user": {
                        "display_name": "Jane Example",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "jane-1234",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550100",
                            "verified": True,
                            "enabled": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                            "capability_tags": "single,group",
                        }
                    ],
                }
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(),
                message_events=MessageEventStore(),
                local_directory=local_store,
                authority_directory=authority_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            result = router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="sms",
                    sender_endpoint="+15550100",
                    text="@alex-2000 Need pickup",
                )
            )

            self.assertEqual(result.sender_resolution, "authority_copy")
            self.assertIsNotNone(local_store.find_user_by_endpoint(network="sms", native_address="+15550100"))
            self.assertEqual(result.sender_entry.identity.display_name, "jane-1234")
            presence = delivery_store.get_user_presence(result.sender_entry.user.user_id)
            self.assertIsNotNone(presence)
            self.assertEqual(presence.last_seen_via_network, "sms")
            self.assertEqual(presence.presence_state, "messaging")

    def test_ingest_rcs_message_matches_sms_endpoint_in_authority_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            authority_store = DirectoryStore(Path(temp_dir) / "authority.db", origin_station_id="authority")
            local_store.initialize()
            authority_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            authority_store.save_directory_user(
                {
                    "user": {
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
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
                            "verified": True,
                            "enabled": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                            "capability_tags": "single,group",
                        }
                    ],
                }
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(),
                message_events=MessageEventStore(),
                local_directory=local_store,
                authority_directory=authority_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            result = router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="rcs",
                    sender_endpoint="+15550999",
                    text="@alex-2000 Need pickup",
                )
            )

            self.assertEqual(result.sender_resolution, "authority_copy")
            self.assertIsNotNone(local_store.find_user_by_endpoint(network="sms", native_address="+15550999"))
            self.assertEqual(result.sender_entry.identity.display_name, "steve-burner")

    def test_ingest_name_only_rcs_message_matches_authority_rcs_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            authority_store = DirectoryStore(Path(temp_dir) / "authority.db", origin_station_id="authority")
            local_store.initialize()
            authority_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            authority_store.save_directory_user(
                {
                    "user": {
                        "display_name": "Steve Burner",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
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
                            "verified": True,
                            "enabled": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                            "capability_tags": "single,group",
                        }
                    ],
                }
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(),
                message_events=MessageEventStore(),
                local_directory=local_store,
                authority_directory=authority_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            result = router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="rcs",
                    sender_endpoint="Steve Burner",
                    text="@alex-2000 Need pickup",
                )
            )

            self.assertEqual(result.sender_resolution, "authority_rcs_name_copy")
            self.assertIsNotNone(result.sender_entry)
            self.assertEqual(result.sender_entry.identity.display_name, "steve-burner")

    def test_unknown_name_only_rcs_creates_rcs_identity_without_sms_address(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )

            message = InboundTextMessage(
                gateway_id="EHN-NNJ", network="rcs", sender_endpoint="Pat Newcomer",
                sender_display_name="Pat Newcomer", text="@alex-2000 check in",
            )
            first = router.ingest_inbound_text_message(message)
            second = router.ingest_inbound_text_message(message)

            self.assertEqual(first.sender_resolution, "provisional")
            self.assertEqual(second.sender_resolution, "local_rcs_name")
            self.assertIsNotNone(first.sender_entry)
            self.assertEqual(first.sender_entry.profile["identity"]["rcs_name"], "Pat Newcomer")
            self.assertEqual(first.sender_entry.endpoints, ())
            self.assertIsNone(store.find_user_by_endpoint(network="sms", native_address="Pat Newcomer"))
            self.assertIsNotNone(store.find_user_by_rcs_name("Pat Newcomer"))

    def test_unknown_meshcore_sender_keeps_advertised_name_and_public_key(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )
            public_key = "12" * 32
            result = router.ingest_inbound_text_message(InboundTextMessage(
                gateway_id="meshcore-usb", network="meshcore", sender_endpoint=public_key,
                sender_display_name="N@DH-TBEAM", text="@burner check in",
            ))
            self.assertEqual(result.sender_resolution, "provisional")
            self.assertEqual(result.sender_entry.user.display_name, "N@DH-TBEAM")
            self.assertEqual(result.sender_entry.endpoints[0].native_address, public_key)
            self.assertEqual(result.sender_entry.endpoints[0].network, "MESHCORE")

    def test_observed_unknown_meshcore_display_name_is_enriched_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            public_key = "34" * 32
            provisional = store.create_provisional_sender(
                display_name="Unknown MESHCORE sender", network="MESHCORE", native_address=public_key,
                actor_class="person", messaging_mode="DIRECT",
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )
            first = router.ingest_inbound_text_message(InboundTextMessage(
                gateway_id="meshcore-usb", network="meshcore", sender_endpoint=public_key,
                sender_display_name="N@DH-TBEAM", text="@burner first named message",
            ))
            second = router.ingest_inbound_text_message(InboundTextMessage(
                gateway_id="meshcore-usb", network="meshcore", sender_endpoint=public_key,
                sender_display_name="Renamed TBEAM", text="@burner later message",
            ))
            self.assertEqual(first.sender_resolution, "local_observed_name_updated")
            self.assertEqual(first.sender_entry.user.user_id, provisional.user.user_id)
            self.assertEqual(first.sender_entry.user.display_name, "N@DH-TBEAM")
            self.assertEqual(first.sender_entry.user.promotion_status, "LOCAL_ONLY")
            self.assertFalse(first.sender_entry.endpoints[0].enabled)
            self.assertEqual(second.sender_resolution, "local")
            self.assertEqual(second.sender_entry.user.display_name, "N@DH-TBEAM")

    def test_meshtastic_gateway_event_carries_advertised_display_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )
            message = router._normalize_gateway_event("meshtastic-usb", {
                "network": "meshtastic", "sender_endpoint": "!1234abcd",
                "sender_display_name": "N@DH-TBEAM", "message_class": "direct_message",
                "text": "@burner check in",
            }, network="meshtastic")
            self.assertEqual(message.sender_endpoint, "!1234abcd")
            self.assertEqual(message.sender_display_name, "N@DH-TBEAM")

    def test_unknown_meshtastic_observation_updates_placeholder_display_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            endpoint = "!1234abcd"
            previous = store.create_provisional_sender(
                display_name="Unknown MESHTASTIC sender", network="MESHTASTIC", native_address=endpoint,
                actor_class="person", messaging_mode="DIRECT",
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )
            result = router.ingest_inbound_text_message(InboundTextMessage(
                gateway_id="meshtastic-usb", network="meshtastic", sender_endpoint=endpoint,
                sender_display_name="N@DH-TBEAM", text="@burner check in",
            ))
            self.assertEqual(result.sender_resolution, "local_observed_name_updated")
            self.assertEqual(result.sender_entry.user.user_id, previous.user.user_id)
            self.assertEqual(result.sender_entry.user.display_name, "N@DH-TBEAM")

    def test_late_radio_name_enriches_only_unknown_observation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            key = "56" * 32
            observed = store.create_provisional_sender(
                display_name="Unknown MESHCORE sender", network="MESHCORE", native_address=key,
                actor_class="person", messaging_mode="DIRECT",
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )
            self.assertTrue(router.enrich_observed_sender_display_name(network="meshcore", endpoint=key, display_name="N2DH-TBEAM"))
            self.assertEqual(store.get_user_by_id(observed.user.user_id).user.display_name, "N2DH-TBEAM")
            self.assertFalse(router.enrich_observed_sender_display_name(network="meshcore", endpoint=key, display_name="Renamed"))

    def test_name_only_rcs_repairs_legacy_provisional_sms_name_endpoint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            legacy = store.create_provisional_sender(
                display_name="Pat Newcomer", network="SMS", native_address="Pat Newcomer",
                actor_class="person", messaging_mode="DIRECT",
            )
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )

            result = router.ingest_inbound_text_message(InboundTextMessage(
                gateway_id="EHN-NNJ", network="rcs", sender_endpoint="Pat Newcomer",
                sender_display_name="Pat Newcomer", text="@alex check in",
            ))

            self.assertEqual(result.sender_resolution, "local_rcs_name_repaired")
            self.assertEqual(result.sender_entry.user.user_id, legacy.user.user_id)
            self.assertEqual(result.sender_entry.profile["identity"]["rcs_name"], "Pat Newcomer")
            self.assertEqual(result.sender_entry.endpoints, ())
            self.assertIsNone(store.find_user_by_endpoint(network="sms", native_address="Pat Newcomer"))
            self.assertEqual(store.find_user_by_rcs_name("Pat Newcomer").user.user_id, legacy.user.user_id)

    def test_register_message_does_not_create_provisional_sender_for_unknown_user(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            local_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(),
                message_events=MessageEventStore(),
                local_directory=local_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            result = router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="sms",
                    sender_endpoint="+15550177",
                    text="register",
                )
            )

            self.assertEqual(result.classification.action, "register")
            self.assertEqual(result.sender_resolution, "unknown_user")
            self.assertIsNone(result.sender_entry)
            self.assertIsNone(local_store.find_user_by_endpoint(network="sms", native_address="+15550177"))

    def test_register_message_copies_sender_from_authority_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            authority_store = DirectoryStore(Path(temp_dir) / "authority.db", origin_station_id="authority")
            local_store.initialize()
            authority_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            authority_store.save_directory_user(
                {
                    "user": {
                        "display_name": "Jane Example",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "jane-1234",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "endpoints": [
                        {
                            "network": "SMS",
                            "native_address": "+15550100",
                            "verified": True,
                            "enabled": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                            "capability_tags": "single,group",
                        }
                    ],
                }
            )
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(),
                message_events=MessageEventStore(),
                local_directory=local_store,
                authority_directory=authority_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            result = router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="sms",
                    sender_endpoint="+15550100",
                    text="register",
                )
            )

            self.assertEqual(result.classification.action, "register")
            self.assertEqual(result.sender_resolution, "authority_copy")
            self.assertIsNotNone(result.sender_entry)
            self.assertIsNotNone(local_store.find_user_by_endpoint(network="sms", native_address="+15550100"))
            presence = delivery_store.get_user_presence(result.sender_entry.user.user_id)
            self.assertIsNotNone(presence)
            self.assertEqual(presence.presence_state, "registered")

    def test_route_message_resolves_mixed_case_local_identity_display_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            steve = store.save_directory_user({
                "user": {"display_name": "Stephen Wilcox", "status": "active"},
                "identity": {"display_name": "Steve", "identity_type": "USER", "messaging_mode": "BOTH"},
                "endpoints": [{"network": "SMS", "native_address": "+19739818016", "enabled": True}],
            })
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(), message_events=MessageEventStore(),
                local_directory=store, delivery_store=delivery_store, station_id="field",
            )

            result = router.ingest_inbound_text_message(InboundTextMessage(
                gateway_id="meshcore-usb", network="meshcore", sender_endpoint="sender-key",
                sender_display_name="Observed sender", text="@steve ok got it",
            ))

            self.assertEqual(result.classification.action, "route")
            self.assertIsNotNone(result.target_entry)
            self.assertEqual(result.target_entry.user.user_id, steve.user.user_id)

    def test_route_message_resolves_target_from_authority_identity_display_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            authority_store = DirectoryStore(Path(temp_dir) / "authority.db", origin_station_id="authority")
            local_store.initialize()
            authority_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            authority_saved = authority_store.save_directory_user(
                {
                    "user": {
                        "display_name": "N2DH Operator",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
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
            router = InboundMessageRouter(
                gateway_registry=GatewayRegistry(),
                message_events=MessageEventStore(),
                local_directory=local_store,
                authority_directory=authority_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            result = router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="sms",
                    sender_endpoint="+15550100",
                    text="@n2dh Need pickup",
                )
            )

            self.assertIsNotNone(result.target_entry)
            self.assertEqual(result.target_entry.user.user_id, authority_saved.user.user_id)
            self.assertEqual(result.target_entry.identity.display_name, "n2dh")

    def test_presence_update_refreshes_waiting_delivery_route_and_triggers_dispatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            local_store = DirectoryStore(Path(temp_dir) / "local.db", origin_station_id="field")
            local_store.initialize()
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            gateway = _FakeGateway()
            registry = GatewayRegistry()
            registry.register(gateway)
            saved_user = local_store.save_directory_user(
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
                            "verified": True,
                            "enabled": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )
            queued = delivery_store.enqueue_message(
                sender_user_id="burner",
                target_user_id="n2dh",
                conversation_scope="single",
                message_class="message",
                plaintext_body="Queued for later",
                accepted_station_id="field",
                owning_station_id="field",
                selected_route="sms",
            )
            router = InboundMessageRouter(
                gateway_registry=registry,
                message_events=MessageEventStore(),
                local_directory=local_store,
                delivery_store=delivery_store,
                station_id="field",
            )

            router.ingest_inbound_text_message(
                InboundTextMessage(
                    gateway_id="EHN-NNJ",
                    network="meshcore",
                    sender_endpoint="PUBKEY-N2DH",
                    text="register",
                )
            )

            refreshed = delivery_store.get_pending_message(queued.message_id)
            self.assertIsNotNone(refreshed)
            self.assertEqual(refreshed.target_endpoint_network, "meshcore")
            self.assertEqual(refreshed.target_endpoint_address, "PUBKEY-N2DH")
            self.assertEqual(refreshed.selected_route, "meshcore")
            self.assertEqual(gateway.dispatch_limits, [10])

    def test_outbound_ham_plaintext_gate_blocks_noncompliant_mesh_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            gateway = _FakeGateway()
            registry = GatewayRegistry()
            registry.register(gateway)
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
                    delivery_id="deliv-1",
                    destination={"public_key": "PUBKEY-ALICE"},
                    payload={"text": "bad\x01payload"},
                ),
            )

            self.assertFalse(result.accepted)
            self.assertEqual(result.status, "blocked_noncompliant")
            self.assertEqual(gateway.sent_requests, [])
            self.assertEqual(events.list_events()[0]["status"], "blocked_noncompliant")

    def test_outbound_ham_plaintext_override_can_render_compliant_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            gateway = _FakeGateway()
            registry = GatewayRegistry()
            registry.register(gateway)
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            orchestrator = DeliveryOrchestrator(
                gateway_registry=registry,
                message_events=MessageEventStore(),
                delivery_store=delivery_store,
                station_id="field-station",
            )

            result = orchestrator.send_directed_private(
                network="meshcore",
                request=DirectedPrivateDeliveryRequest(
                    delivery_id="deliv-2",
                    destination={"public_key": "PUBKEY-ALICE"},
                    payload={"text": "bad\x01payload", "ham_plaintext": "Weather clear at shelter"},
                ),
            )

            self.assertTrue(result.accepted)
            self.assertEqual(gateway.sent_requests[0].payload["text"], "Weather clear at shelter")

    def test_orchestrator_queues_when_target_is_known_but_gateway_is_unavailable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            delivery_store = DeliveryStore(Path(temp_dir) / "delivery.db")
            delivery_store.initialize()
            events = MessageEventStore()
            orchestrator = DeliveryOrchestrator(
                gateway_registry=GatewayRegistry(),
                message_events=events,
                delivery_store=delivery_store,
                station_id="field-station",
            )

            result = orchestrator.send_directed_private(
                network="meshcore",
                request=DirectedPrivateDeliveryRequest(
                    delivery_id="deliv-queued-1",
                    destination={"public_key": "PUBKEY-ALICE"},
                    payload={"text": "queued text"},
                    metadata={
                        "source_user_id": "burner",
                        "target_user_id": "n2dh",
                        "conversation_scope": "single",
                    },
                ),
            )

            self.assertTrue(result.accepted)
            self.assertEqual(result.status, "accepted_but_queued")
            self.assertEqual(len(delivery_store.list_pending_messages()), 1)
            self.assertEqual(events.list_events()[0]["status"], "accepted_but_queued")


if __name__ == "__main__":
    unittest.main()