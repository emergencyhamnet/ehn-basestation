from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.app.authority_server import (
    build_authority_delivery_messages_payload,
    build_authority_audit_payload,
    build_authority_stations_payload,
    build_authority_summary_payload,
    build_authority_users_payload,
    claim_authority_delivery_message_payload,
    complete_authority_delivery_message_payload,
    create_authority_delivery_message_payload,
    release_authority_delivery_message_payload,
    renew_authority_delivery_message_payload,
    save_authority_station_payload,
    save_authority_user_payload,
)
from ehn_basestation.app.settings import BasestationSettings


class AuthorityServerPayloadTests(unittest.TestCase):
    def test_authority_payloads_use_separate_authority_db(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "local" / "directory" / "ehn-directory.db"),
                    "EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH": str(root / "var" / "authority" / "directory" / "ehn-authority.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "0",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                },
            )

            status, _response = save_authority_user_payload(
                settings,
                {
                    "user": {"display_name": "Authority User", "status": "active", "home_region": "NNJ", "is_local_to_station": False, "retention_class": "TRUSTED_STATION", "promotion_status": "PROMOTED"},
                    "identity": {"display_name": "authority-user", "identity_type": "USER", "messaging_mode": "BOTH"},
                    "endpoints": [{"network": "EMAIL", "native_address": "authority@example.org", "enabled": True, "verified": False, "priority": 100, "trust_level": "draft", "replication_class": "TRUSTED_STATION"}],
                },
            )

            self.assertEqual(status.value, 200)
            summary = build_authority_summary_payload(settings)
            users = build_authority_users_payload(settings)
            audit = build_authority_audit_payload(settings, limit=10)

            self.assertEqual(summary["authority"]["user_count"], 1)
            self.assertEqual(users["source"], "authority-db")
            self.assertEqual(users["users"][0]["identity"]["display_name"], "authority-user")
            self.assertEqual(audit["audit_events"][0]["action"], "save_authority_user")

    def test_authority_station_payloads_use_registry_db(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "0",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                },
            )

            status, response = save_authority_station_payload(
                settings,
                {
                    "station": {
                        "station_id": "station-nnj-01",
                        "display_name": "NNJ Station 01",
                        "role": "field",
                        "db_path": str(root / "var" / "stations" / "field" / "directory" / "ehn-directory.db"),
                        "owner_name": "Ops Lead",
                        "owner_org": "EHN",
                        "deployment_status": "active",
                    },
                    "capabilities": {
                        "supports_meshcore": True,
                        "supports_sms_gateway": True,
                        "supports_queue_claims": True,
                    },
                },
            )

            self.assertEqual(status.value, 200)
            self.assertEqual(response["station"]["station_id"], "station-nnj-01")
            stations = build_authority_stations_payload(settings)
            self.assertEqual(stations["source"], "registry-db")
            self.assertEqual(stations["station_count"], 1)
            self.assertEqual(stations["stations"][0]["owner_name"], "Ops Lead")

    def test_authority_delivery_queue_claim_flow_redacts_message_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "0",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                    "EHN_BASESTATION_STATION_ID": "authority-main",
                },
            )

            create_status, created = create_authority_delivery_message_payload(
                settings,
                {
                    "sender_user_id": "burner",
                    "target_user_id": "n2dh",
                    "target_endpoint_network": "meshcore",
                    "target_endpoint_address": "PUBKEY-N2DH",
                    "conversation_scope": "single",
                    "message_class": "message",
                    "plaintext_body": "Need update",
                    "selected_route": "meshcore",
                },
            )
            self.assertEqual(create_status.value, 200)
            self.assertNotIn("plaintext_body", created["entry"])
            self.assertNotIn("claim_token", created["entry"])
            self.assertNotIn("target_endpoint_address", created["entry"])
            self.assertTrue(created["entry"]["has_target_endpoint"])
            message_id = created["entry"]["message_id"]

            queued = build_authority_delivery_messages_payload(settings)
            self.assertEqual(queued["message_count"], 1)
            self.assertNotIn("plaintext_body", queued["messages"][0])
            self.assertEqual(queued["route_counts"]["meshcore"], 1)
            self.assertEqual(queued["state_counts"]["waiting_for_path"], 1)

            claim_status, claim_payload = claim_authority_delivery_message_payload(
                settings,
                message_id,
                {"station_id": "station-nnj-01", "claim_window_seconds": 60},
            )
            self.assertEqual(claim_status.value, 200)
            claim_token = claim_payload["entry"]["claim_token"]

            renew_status, _renew_payload = renew_authority_delivery_message_payload(
                settings,
                message_id,
                {"station_id": "station-nnj-01", "claim_token": claim_token, "claim_window_seconds": 60},
            )
            self.assertEqual(renew_status.value, 200)

            release_status, release_payload = release_authority_delivery_message_payload(
                settings,
                message_id,
                {
                    "station_id": "station-nnj-01",
                    "claim_token": claim_token,
                    "route_name": "meshcore",
                    "next_attempt_after": "2026-09-06T19:00:00+00:00",
                },
            )
            self.assertEqual(release_status.value, 200)
            self.assertEqual(release_payload["entry"]["delivery_state"], "waiting_for_path")

            queued_after_release = build_authority_delivery_messages_payload(settings)
            self.assertEqual(queued_after_release["claimed_message_count"], 0)

            reclaim_status, reclaim_payload = claim_authority_delivery_message_payload(
                settings,
                message_id,
                {"station_id": "station-nnj-02", "claim_window_seconds": 60},
            )
            self.assertEqual(reclaim_status.value, 200)

            complete_status, complete_payload = complete_authority_delivery_message_payload(
                settings,
                message_id,
                {
                    "station_id": "station-nnj-02",
                    "claim_token": reclaim_payload["entry"]["claim_token"],
                    "outcome": "delivered",
                    "route_name": "meshcore",
                    "gateway_request_id": "gw-1",
                },
            )
            self.assertEqual(complete_status.value, 200)
            self.assertEqual(complete_payload["entry"]["delivery_state"], "delivered")


if __name__ == "__main__":
    unittest.main()