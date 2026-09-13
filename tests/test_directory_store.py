from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
import sqlite3
import unittest

from ehn_basestation.directory.directory_store import DirectoryStore


class DirectoryStoreTests(unittest.TestCase):
    def test_initialize_creates_trial_directory_tables(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="field-station")

            store.initialize()

            with closing(sqlite3.connect(database_path)) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

            self.assertTrue({"users", "identities", "endpoints", "replication_state", "audit_log"}.issubset(table_names))

    def test_create_local_user_persists_user_identity_endpoint_and_review_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="pi-field-station")
            store.initialize()

            entry = store.create_local_user(
                display_name="Alex Example",
                home_region="east",
                endpoint_network="meshcore",
                endpoint_address="abcdef123456",
                promotion_status="PENDING_AUTHORITY_REVIEW",
            )

            self.assertEqual(entry.user.display_name, "Alex Example")
            self.assertEqual(entry.user.promotion_status, "PENDING_AUTHORITY_REVIEW")
            self.assertEqual(entry.identity.display_name, "Alex Example")
            self.assertEqual(len(entry.endpoints), 1)
            self.assertEqual(entry.endpoints[0].network, "MESHCORE")

            rows = store.list_local_users(promotion_status="PENDING_AUTHORITY_REVIEW")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].user.user_id, entry.user.user_id)
            self.assertEqual(rows[0].endpoints[0].native_address, "abcdef123456")

    def test_save_directory_user_persists_profile_and_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="windows-authority")
            store.initialize()

            entry = store.save_directory_user(
                {
                    "user": {
                        "display_name": "Jane Example",
                        "status": "active",
                        "home_region": "East Sector",
                        "is_local_to_station": False,
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "jane-1234",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                        "policy_id": "standard-member",
                    },
                    "profile": {
                        "personal": {
                            "given_name": "Jane",
                            "family_name": "Example",
                            "email": "jane@example.org",
                            "phone_primary": "+1-555-0100",
                            "home_location_text": "Bristol safe house",
                            "lat": 51.4545,
                            "lon": -2.5879,
                            "member_status": "Member",
                            "last_active_at": "2026-09-01T14:22:00+00:00",
                        },
                        "identity": {
                            "routing_notes": "direct allowed, group allowed",
                            "rcs_name": "Steve Burner",
                        },
                        "teams": {
                            "teams": ["East Ops"],
                            "roles": ["Field Medic"],
                            "active_group_member": True,
                        },
                        "notes": {
                            "operator_notes": "certified medic",
                        },
                    },
                    "endpoints": [
                        {
                            "network": "MESHCORE",
                            "native_address": "abcdef123456",
                            "verified": True,
                            "enabled": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                            "last_verified_at": "2026-08-31T18:10:00+00:00",
                            "capability_tags": "group,direct",
                            "metadata": {
                                "default_channel": "ops-east",
                                "advert_name": "Jane Mobile",
                            },
                        }
                    ],
                }
            )

            self.assertEqual(entry.identity.display_name, "jane-1234")
            self.assertEqual(entry.profile["personal"]["email"], "jane@example.org")
            self.assertEqual(entry.profile["personal"]["preferred_language"], "en")
            self.assertEqual(entry.profile["identity"]["routing_notes"], "direct allowed, group allowed")
            self.assertEqual(entry.profile["identity"]["rcs_name"], "Steve Burner")
            self.assertEqual(entry.profile["teams"]["teams"], ["East Ops"])
            self.assertEqual(entry.profile["endpoint_metadata"]["MESHCORE"]["default_channel"], "ops-east")

            audit_rows = store.list_audit_events(limit=5)
            self.assertEqual(audit_rows[0]["action"], "save_directory_user")
            self.assertEqual(audit_rows[0]["subject_type"], "user")

    def test_save_directory_user_persists_preferred_language(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="windows-authority")
            store.initialize()

            entry = store.save_directory_user(
                {
                    "user": {
                        "display_name": "Language User",
                        "status": "active",
                        "home_region": "NNJ",
                        "is_local_to_station": False,
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "language-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "personal": {
                            "preferred_language": "es",
                        },
                    },
                    "endpoints": [],
                }
            )

            self.assertEqual(entry.profile["personal"]["preferred_language"], "es")

    def test_find_user_by_rcs_name_returns_exact_match(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="windows-authority")
            store.initialize()

            saved = store.save_directory_user(
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
                            "enabled": True,
                            "verified": True,
                            "priority": 100,
                            "trust_level": "verified",
                            "replication_class": "TRUSTED_STATION",
                        }
                    ],
                }
            )

            matched = store.find_user_by_rcs_name("Steve Burner")

            self.assertIsNotNone(matched)
            self.assertEqual(matched.user.user_id, saved.user.user_id)

    def test_find_user_by_identity_display_name_returns_exact_match(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="windows-authority")
            store.initialize()

            saved = store.save_directory_user(
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

            matched = store.find_user_by_identity_display_name("n2dh")

            self.assertIsNotNone(matched)
            self.assertEqual(matched.user.user_id, saved.user.user_id)

    def test_save_directory_user_reuses_existing_identity_when_user_id_is_provided(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="windows-authority")
            store.initialize()

            original = store.save_directory_user(
                {
                    "user": {
                        "user_id": "probe-user",
                        "display_name": "Probe User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "probe-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {
                            "rcs_name": "Original Name",
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
                        }
                    ],
                }
            )

            updated = store.save_directory_user(
                {
                    "user": {
                        "user_id": "probe-user",
                        "display_name": "Probe User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "display_name": "probe-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {
                            "rcs_name": "Updated Name",
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
                        }
                    ],
                }
            )

            self.assertEqual(updated.identity.identity_id, original.identity.identity_id)
            self.assertEqual(updated.profile["identity"]["rcs_name"], "Updated Name")

            with closing(sqlite3.connect(database_path)) as connection:
                identity_count = connection.execute(
                    "SELECT COUNT(*) FROM identities WHERE user_id = ?",
                    ("probe-user",),
                ).fetchone()[0]

            self.assertEqual(identity_count, 1)

    def test_list_users_returns_latest_identity_row_per_user(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="windows-authority")
            store.initialize()

            first = store.save_directory_user(
                {
                    "user": {
                        "user_id": "probe-user",
                        "display_name": "Probe User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "identity_id": "identity-old",
                        "display_name": "probe-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {
                            "rcs_name": "Old Name",
                        },
                    },
                    "endpoints": [],
                }
            )
            second = store.save_directory_user(
                {
                    "user": {
                        "user_id": "probe-user",
                        "display_name": "Probe User",
                        "status": "active",
                        "home_region": "NNJ",
                        "retention_class": "TRUSTED_STATION",
                        "promotion_status": "PROMOTED",
                    },
                    "identity": {
                        "identity_id": "identity-new",
                        "display_name": "probe-user",
                        "identity_type": "USER",
                        "messaging_mode": "BOTH",
                    },
                    "profile": {
                        "identity": {
                            "rcs_name": "New Name",
                        },
                    },
                    "endpoints": [],
                }
            )

            listed = store.list_users()

            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].identity.identity_id, second.identity.identity_id)
            self.assertEqual(listed[0].profile["identity"]["rcs_name"], "New Name")

    def test_reset_directory_users_clears_records_and_retains_audit_event(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="field-station")
            store.initialize()
            store.create_local_user(display_name="Reset Me", audit_actor="test-suite")

            counts = store.reset_directory_users(audit_actor="test-suite")

            self.assertEqual(counts["users"], 1)
            self.assertEqual(store.list_users(), [])
            self.assertEqual(store.list_audit_events(limit=5)[0]["action"], "reset_directory_users")

    def test_seed_directory_users_imports_multiple_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="authority-main")
            store.initialize()

            saved = store.seed_directory_users(
                [
                    {
                        "user": {"display_name": "Seed One", "status": "active", "home_region": "NNJ", "is_local_to_station": False, "retention_class": "TRUSTED_STATION", "promotion_status": "PROMOTED"},
                        "identity": {"display_name": "seed-one", "identity_type": "USER", "messaging_mode": "BOTH"},
                        "endpoints": [{"network": "SMS", "native_address": "+15550101", "enabled": True, "verified": False, "priority": 100, "trust_level": "draft", "replication_class": "TRUSTED_STATION"}],
                    },
                    {
                        "user": {"display_name": "Seed Two", "status": "active", "home_region": "NNJ", "is_local_to_station": False, "retention_class": "TRUSTED_STATION", "promotion_status": "PROMOTED"},
                        "identity": {"display_name": "seed-two", "identity_type": "USER", "messaging_mode": "BOTH"},
                        "endpoints": [{"network": "EMAIL", "native_address": "seed-two@example.org", "enabled": True, "verified": False, "priority": 100, "trust_level": "draft", "replication_class": "TRUSTED_STATION"}],
                    },
                ],
                audit_actor="test-suite",
            )

            self.assertEqual(len(saved), 2)
            self.assertEqual(len(store.list_users()), 2)
            self.assertEqual(store.list_audit_events(limit=10)[0]["action"], "seed_directory_user")

    def test_list_local_users_can_exclude_observed_retention_class(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ehn-directory.db"
            store = DirectoryStore(database_path, origin_station_id="field-station")
            store.initialize()

            store.create_local_user(
                display_name="Review Candidate",
                promotion_status="PENDING_AUTHORITY_REVIEW",
            )
            store.create_provisional_sender(
                display_name="Unknown SMS sender",
                network="SMS",
                native_address="+15559999",
                actor_class="person",
                messaging_mode="BOTH",
            )

            filtered = store.list_local_users(
                promotion_status="PENDING_AUTHORITY_REVIEW",
                exclude_retention_classes=("FIELD_OBSERVED",),
            )
            unfiltered = store.list_local_users(
                promotion_status="PENDING_AUTHORITY_REVIEW",
            )

            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0].user.display_name, "Review Candidate")
            self.assertEqual(len(unfiltered), 2)


if __name__ == "__main__":
    unittest.main()