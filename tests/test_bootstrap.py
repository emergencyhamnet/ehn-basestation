import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ehn_basestation.app.bootstrap import create_app
from ehn_basestation.app.settings import BasestationSettings


class BootstrapTests(unittest.TestCase):
    def test_create_app_initializes_core_services(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = create_app(
                settings=BasestationSettings.from_env(
                    project_root=Path(temp_dir),
                    env={"BASESTATION_MESHCORE_ENABLED": "0", "BASESTATION_SMS_ENABLED": "0"},
                )
            )

            self.assertEqual(app.gateway_registry.list_gateways(), [])
            self.assertEqual(app.message_events.list_events(), [])
            self.assertEqual(app.settings.active_directory_db_path.name, "ehn-directory.db")
            self.assertEqual(app.settings.authority_directory_db_path.name, "ehn-authority.db")
            self.assertEqual(app.settings.peer_directory_db_path.name, "ehn-directory.db")
            self.assertEqual(app.settings.registry_db_path.name, "ehn-stations.db")
            self.assertEqual(app.settings.delivery_db_path.name, "ehn-delivery.db")
            self.assertEqual(app.directory_store.list_users(), [])
            self.assertEqual(app.authority_directory_store.list_users(), [])
            self.assertEqual(app.station_registry_store.list_station_records(), [])
            self.assertEqual(app.delivery_store.list_pending_messages(), [])
            self.assertIsNotNone(app.delivery_queue)

    def test_create_app_registers_meshcore_gateway_when_enabled(self) -> None:
        app = create_app(
            settings=BasestationSettings.from_env(
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "0",
                    "BASESTATION_MESHCORE_BASE_URL": "",
                }
            )
        )

        gateways = app.gateway_registry.list_gateways()
        self.assertEqual(len(gateways), 1)
        self.assertEqual(gateways[0]["gateway_id"], "meshcore-gateway-1")
        self.assertEqual(gateways[0]["status"], "unconfigured")
        self.assertIsNotNone(app.inbound_router)

    def test_create_app_can_select_stream_gateway_link(self) -> None:
        app = create_app(
            settings=BasestationSettings.from_env(
                env={
                    "BASESTATION_MESHCORE_ENABLED": "1",
                    "BASESTATION_SMS_ENABLED": "0",
                    "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
                    "BASESTATION_MESHCORE_LINK_ENDPOINT": "127.0.0.1:9",
                    "BASESTATION_MESHCORE_BASE_URL": "",
                }
            )
        )

        gateways = app.gateway_registry.list_gateways()
        self.assertEqual(len(gateways), 1)
        self.assertEqual(gateways[0]["transport"], "meshcore-gateway-link")
        self.assertEqual(gateways[0]["status"], "unreachable")

    def test_create_app_registers_sms_gateway_when_enabled(self) -> None:
        app = create_app(
            settings=BasestationSettings.from_env(
                env={
                    "BASESTATION_MESHCORE_ENABLED": "0",
                    "BASESTATION_SMS_ENABLED": "1",
                    "BASESTATION_SMS_GATEWAY_ID": "android-sms-1",
                }
            )
        )

        gateways = app.gateway_registry.list_gateways()
        self.assertEqual(len(gateways), 1)
        self.assertEqual(gateways[0]["gateway_id"], "android-sms-1")
        self.assertEqual(gateways[0]["transport"], "android-http-edge")
        self.assertIsNotNone(app.inbound_router)



if __name__ == "__main__":
    unittest.main()
