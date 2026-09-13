from __future__ import annotations

from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import unittest

from ehn_basestation.app.settings import BasestationSettings
from ehn_basestation.app.station_link import (
    PeerStationLinkServer,
    initialize_station_db,
    list_station_records,
    sync_station_with_peer,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class StationLinkTests(unittest.TestCase):
    def test_init_station_db_writes_self_record(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "local-station",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )

            record = initialize_station_db(settings)
            rows = list_station_records(settings)

            self.assertEqual(record.station_id, "local-station")
            self.assertEqual(record.role, "authority")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"], "self")
            self.assertEqual(settings.registry_db_path.name, "ehn-stations.db")

    def test_sync_station_with_peer_populates_both_databases(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "local-station",
                    "EHN_BASESTATION_STATION_ROLE": "authority",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "local" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )
            peer_settings = BasestationSettings.from_env(
                project_root=root,
                env={
                    "EHN_BASESTATION_STATION_NAME": "peer-station",
                    "EHN_BASESTATION_STATION_ROLE": "field",
                    "BASESTATION_DIRECTORY_DB_PATH": str(root / "var" / "stations" / "peer" / "directory" / "ehn-directory.db"),
                    "BASESTATION_MESHCORE_ENABLED": "0",
                },
            )
            port = _free_port()
            server = PeerStationLinkServer(peer_settings, host="127.0.0.1", port=port)
            worker = threading.Thread(target=server.serve_once, daemon=True)
            worker.start()

            result = sync_station_with_peer(local_settings, host="127.0.0.1", port=port)
            worker.join(timeout=2.0)

            local_rows = list_station_records(local_settings)
            peer_rows = list_station_records(peer_settings)

            self.assertEqual(result.remote_station["station_id"], "peer-station")
            self.assertEqual(result.remote_station["role"], "field")
            self.assertEqual({row["station_id"] for row in local_rows}, {"local-station", "peer-station"})
            self.assertEqual({row["station_id"] for row in peer_rows}, {"local-station", "peer-station"})



if __name__ == "__main__":
    unittest.main()