from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from ehn_basestation.app.cli import _build_review_bundle, _build_seed_bundle, _default_excluded_retention_classes
from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.directory.directory_store import DirectoryStore


class ReviewBundleTests(unittest.TestCase):
    def test_build_review_bundle_includes_origin_and_candidates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "pi-field-station",
                    "EHN_BASESTATION_STATION_ROLE": "field",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "pi-field" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )
            store = DirectoryStore(settings.active_directory_db_path, origin_station_id=settings.station_name)
            store.initialize()
            store.create_local_user(
                display_name="Taylor Example",
                promotion_status="PENDING_AUTHORITY_REVIEW",
            )

            rows = store.list_local_users(promotion_status="PENDING_AUTHORITY_REVIEW")
            bundle = _build_review_bundle(settings, rows)

            self.assertEqual(bundle["bundle_type"], "ehn-review-candidates")
            self.assertEqual(bundle["origin_station"], "pi-field-station")
            self.assertEqual(bundle["origin_role"], "field")
            self.assertEqual(len(bundle["review_candidates"]), 1)

            serialized = json.dumps(bundle)
            self.assertIn("Taylor Example", serialized)

    def test_build_seed_bundle_includes_users_in_seed_shape(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "pi-field-station",
                    "EHN_BASESTATION_STATION_ROLE": "field",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "pi-field" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )
            store = DirectoryStore(settings.active_directory_db_path, origin_station_id=settings.station_name)
            store.initialize()
            store.create_local_user(
                display_name="Jordan Seed",
                promotion_status="LOCAL_ONLY",
            )

            rows = store.list_users()
            bundle = _build_seed_bundle(settings, rows, source="local")

            self.assertEqual(bundle["bundle_type"], "ehn-directory-seed")
            self.assertEqual(bundle["source"], "local")
            self.assertEqual(len(bundle["users"]), 1)

            serialized = json.dumps(bundle)
            self.assertIn("Jordan Seed", serialized)

    def test_default_review_export_excludes_observed_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "pi-field-station",
                    "EHN_BASESTATION_STATION_ROLE": "field",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "pi-field" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )
            store = DirectoryStore(settings.active_directory_db_path, origin_station_id=settings.station_name)
            store.initialize()
            store.create_local_user(
                display_name="Taylor Example",
                promotion_status="PENDING_AUTHORITY_REVIEW",
            )
            store.create_provisional_sender(
                display_name="Unknown SMS sender",
                network="SMS",
                native_address="+15550000",
                actor_class="person",
                messaging_mode="BOTH",
            )

            rows = store.list_local_users(
                promotion_status="PENDING_AUTHORITY_REVIEW",
                exclude_retention_classes=_default_excluded_retention_classes(include_observed=False),
            )
            bundle = _build_review_bundle(settings, rows)

            self.assertEqual(len(bundle["review_candidates"]), 1)
            serialized = json.dumps(bundle)
            self.assertIn("Taylor Example", serialized)
            self.assertNotIn("Unknown SMS sender", serialized)


if __name__ == "__main__":
    unittest.main()