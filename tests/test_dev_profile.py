from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ehn_basestation.app.dev_profile import initialize_windows_dev_layout
from ehn_basestation.app.settings import BasestationSettings, load_env_file


class DevelopmentProfileTests(unittest.TestCase):
    def test_init_dev_layout_creates_two_station_scaffold(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
                    "BASESTATION_MESHCORE_LINK_ENDPOINT": "192.168.1.219:7463",
                    "BASESTATION_MESHCORE_BASE_URL": "http://192.168.1.219:8088",
                },
            )

            layout = initialize_windows_dev_layout(settings=settings)

            self.assertTrue(settings.active_directory_db_path.parent.exists())
            self.assertTrue(settings.authority_directory_db_path.parent.exists())
            self.assertTrue(settings.peer_directory_db_path.parent.exists())
            self.assertTrue(settings.replication_root.exists())
            self.assertEqual(len(layout.written_files), 3)

            local_env = (root / "dev" / "windows" / "env" / "local-station.env").read_text(encoding="utf-8")
            peer_env = (root / "dev" / "windows" / "env" / "peer-station.env").read_text(encoding="utf-8")
            authority_env = (root / "dev" / "windows" / "env" / "authority-station.env").read_text(encoding="utf-8")

            self.assertIn("EHN_BASESTATION_STATION_NAME=local-station", local_env)
            self.assertIn("EHN_BASESTATION_STATION_ROLE=field", local_env)
            self.assertIn(str(settings.active_directory_db_path), local_env)
            self.assertIn(str(settings.authority_directory_db_path), local_env)
            self.assertIn("EHN_BASESTATION_LINK_PORT=7462", local_env)
            self.assertIn("EHN_BASESTATION_PEER_PORT=7462", local_env)
            self.assertIn("BASESTATION_MESHCORE_LINK_CARRIER=tcp", local_env)
            self.assertIn("BASESTATION_MESHCORE_LINK_ENDPOINT=192.168.1.219:7463", local_env)
            self.assertIn("BASESTATION_MESHCORE_BASE_URL=http://192.168.1.219:8088", local_env)
            self.assertIn("EHN_BASESTATION_STATION_NAME=peer-station", peer_env)
            self.assertIn("EHN_BASESTATION_STATION_ROLE=field", peer_env)
            self.assertIn("BASESTATION_MESHCORE_ENABLED=0", peer_env)
            self.assertIn("EHN_BASESTATION_STATION_NAME=authority-station", authority_env)
            self.assertIn("EHN_BASESTATION_STATION_ROLE=authority", authority_env)
            self.assertIn(str(settings.authority_directory_db_path), authority_env)

    def test_load_env_file_applies_generated_local_profile(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
                    "BASESTATION_MESHCORE_LINK_ENDPOINT": "192.168.1.219:7463",
                    "BASESTATION_MESHCORE_BASE_URL": "http://192.168.1.219:8088",
                },
            )
            initialize_windows_dev_layout(settings=settings)

            env_file = root / "dev" / "windows" / "env" / "local-station.env"
            loaded = load_env_file(env_file)
            resolved = BasestationSettings.from_env(project_root=root)

            self.assertEqual(loaded["BASESTATION_MESHCORE_LINK_CARRIER"], "tcp")
            self.assertEqual(loaded["BASESTATION_MESHCORE_LINK_ENDPOINT"], "192.168.1.219:7463")
            self.assertEqual(loaded["BASESTATION_MESHCORE_BASE_URL"], "http://192.168.1.219:8088")
            self.assertEqual(resolved.meshcore_link_carrier, "tcp")
            self.assertEqual(resolved.meshcore_link_endpoint, "192.168.1.219:7463")
            self.assertEqual(resolved.meshcore_base_url, "http://192.168.1.219:8088")
            self.assertEqual(resolved.station_role, "field")
            self.assertEqual(resolved.station_link_port, 7462)
            self.assertEqual(resolved.peer_station_port, 7462)
            self.assertEqual(resolved.active_directory_db_path, settings.active_directory_db_path)
            self.assertEqual(resolved.authority_directory_db_path, settings.authority_directory_db_path)


if __name__ == "__main__":
    unittest.main()