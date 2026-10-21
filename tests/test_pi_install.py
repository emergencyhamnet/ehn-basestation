from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


def _module(name):
    path = Path(__file__).resolve().parents[1] / "dev" / "linux" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PiInstallTests(unittest.TestCase):
    def test_local_only_profile_and_user_services_preserve_token(self):
        configure = _module("configure_pi").configure
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            home = Path(temporary) / "home"
            units = configure(root, home, "/dev/serial/by-id/meshcore", "/dev/serial/by-id/meshtastic", "phone")
            profile = root / "var" / "pi" / "station.env"
            contents = profile.read_text()
            values = dict(line.split("=", 1) for line in contents.splitlines())
            self.assertEqual(values["BASESTATION_DIRECTORY_DB_PATH"], values["EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH"])
            self.assertEqual(values["BASESTATION_MESHCORE_LINK_ENDPOINT"], "127.0.0.1:7465")
            self.assertGreaterEqual(len(values["BASESTATION_SMS_ANDROID_AUTH_TOKEN"]), 40)
            self.assertEqual(len(units), 3)
            self.assertIn(f"WorkingDirectory={root}\n", units[0].read_text())
            self.assertNotIn('WorkingDirectory="', units[0].read_text())
            self.assertIn("--allow-transmit", units[0].read_text())
            self.assertIn("--host 0.0.0.0", units[1].read_text())
            configure(root, home, "/dev/serial/by-id/meshcore", "/dev/serial/by-id/meshtastic", "phone")
            self.assertEqual(profile.read_text(), contents)
            with self.assertRaises(ValueError):
                configure(root, home, "/dev/serial/by-id/changed", "/dev/serial/by-id/meshtastic", "phone")

    def test_invalid_or_shared_device_paths_are_rejected(self):
        configure = _module("configure_pi").configure
        with TemporaryDirectory() as temporary:
            for meshcore, meshtastic in (("COM12", "/dev/ttyACM1"), ("/dev/ttyACM0", "/dev/ttyACM0"), ("/dev/a\nextra", "/dev/b")):
                with self.subTest(meshcore=meshcore), self.assertRaises(ValueError):
                    configure(Path(temporary), Path(temporary), meshcore, meshtastic, "phone")

    def test_tunnel_detection_requires_matching_phone_and_host_ports(self):
        present = _module("android_usb_link").tunnel_present
        self.assertTrue(present("UsbFfs tcp:18098 tcp:18098\n", 18098))
        self.assertFalse(present("UsbFfs tcp:18098 tcp:8099\n", 18098))
        self.assertFalse(present("", 18098))


if __name__ == "__main__":
    unittest.main()