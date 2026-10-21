from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sys


def _value(text: str) -> str:
    if not text or any(char in text for char in "\r\n\x00"):
        raise ValueError("Configuration values must be nonempty single lines.")
    return text


def _quote(text: str) -> str:
    return '"' + _value(text).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def configure(root: Path, home: Path, meshcore_port: str, meshtastic_port: str, android_serial: str) -> list[Path]:
    root = root.resolve()
    home = home.resolve()
    for value in (str(root), str(home), meshcore_port, meshtastic_port, android_serial):
        _value(value)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", android_serial):
        raise ValueError("Use the physical USB phone's ADB serial, not a network address.")
    if meshcore_port == meshtastic_port:
        raise ValueError("The radios must use different serial devices.")
    if not all(PurePosixPath(port).is_absolute() for port in (meshcore_port, meshtastic_port)):
        raise ValueError("Radio paths must be absolute; use /dev/serial/by-id/ paths.")
    config = root / "var" / "pi"
    config.mkdir(parents=True, exist_ok=True)
    os.chmod(config, 0o700)
    env_file = config / "station.env"
    if env_file.exists():
        values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line and not line.startswith("#"))
        devices = {
            "EHN_MESHCORE_USB_SERIAL_PORT": meshcore_port,
            "BASESTATION_MESHTASTIC_SERIAL_PORT": meshtastic_port,
            "EHN_ANDROID_USB_SERIAL": android_serial,
        }
        if any(values.get(key) != value for key, value in devices.items()):
            raise ValueError("Selected devices differ from the existing profile; reconcile it explicitly before rerunning.")
    else:
        directory = root / "var" / "stations" / "local" / "directory" / "ehn-directory.db"
        values = {
            "EHN_BASESTATION_INSTANCE_ROOT": str(root / "var"),
            "EHN_BASESTATION_STATION_NAME": "pi-island",
            "EHN_BASESTATION_STATION_ID": "pi-island",
            "EHN_BASESTATION_STATION_ROLE": "field",
            "BASESTATION_DIRECTORY_DB_PATH": str(directory),
            "EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH": str(directory),
            "BASESTATION_MESHCORE_ENABLED": "1",
            "BASESTATION_MESHCORE_LINK_CARRIER": "tcp",
            "BASESTATION_MESHCORE_LINK_ENDPOINT": "127.0.0.1:7465",
            "BASESTATION_MESHCORE_BASE_URL": "",
            "BASESTATION_MESHCORE_GATEWAY_ID": "meshcore-pi-usb",
            "BASESTATION_MESHCORE_DISPLAY_NAME": "Pi MeshCore USB",
            "BASESTATION_MESHCORE_TIMEOUT_SECONDS": "5",
            "EHN_MESHCORE_USB_SERIAL_PORT": meshcore_port,
            "BASESTATION_MESHTASTIC_ENABLED": "1",
            "BASESTATION_MESHTASTIC_GATEWAY_ID": "meshtastic-pi-usb",
            "BASESTATION_MESHTASTIC_DISPLAY_NAME": "Pi Meshtastic USB",
            "BASESTATION_MESHTASTIC_SERIAL_PORT": meshtastic_port,
            "BASESTATION_MESHTASTIC_GROUP_CHANNEL_INDEX": "1",
            "BASESTATION_SMS_ENABLED": "1",
            "BASESTATION_SMS_GATEWAY_ID": "EHN-PI3",
            "BASESTATION_SMS_DISPLAY_NAME": "Pi Android SMS",
            "BASESTATION_SMS_ANDROID_AUTH_TOKEN": secrets.token_urlsafe(32),
            "EHN_ANDROID_USB_SERIAL": android_serial,
            "EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS": "60",
        }
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    os.chmod(env_file, 0o600)
    python = _quote(str(root / ".venv" / "bin" / "python"))
    unit_root = home / ".config" / "systemd" / "user"
    unit_root.mkdir(parents=True, exist_ok=True)
    commands = {
        "ehn-meshcore-usb": f"{python} -m ehn_basestation.gateways.meshcore.usb_gateway --serial-port {_quote(meshcore_port)} --port 7465 --gateway-id meshcore-pi-usb --allow-transmit",
        "ehn-basestation": f"{python} -m ehn_basestation.app.cli --env-file {_quote(str(env_file))} run-workbench-preview --host 0.0.0.0 --port 18098",
        "ehn-android-usb": f"{python} {_quote(str(root / 'dev' / 'linux' / 'android_usb_link.py'))} --serial {_quote(android_serial)} --port 18098",
    }
    units = []
    for name, command in commands.items():
        dependencies = "After=ehn-meshcore-usb.service\nWants=ehn-meshcore-usb.service\n" if name == "ehn-basestation" else ""
        unit = unit_root / f"{name}.service"
        unit.write_text(
            f"[Unit]\nDescription=EHN {name}\n{dependencies}\n"
            f"[Service]\nType=simple\nWorkingDirectory={root}\n"
            f"ExecStart={command}\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=15\n"
            "UMask=0077\nEnvironment=PYTHONUNBUFFERED=1\n\n[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        units.append(unit)
    return units


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the local-only Pi alpha as the service user, without copying Windows data.")
    parser.add_argument("--meshcore-port", required=True)
    parser.add_argument("--meshtastic-port", required=True)
    parser.add_argument("--android-serial", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    configure(root, Path.home(), args.meshcore_port, args.meshtastic_port, args.android_serial)
    sys.path.insert(0, str(root / "src"))
    from ehn_basestation.app.settings import BasestationSettings, load_env_file
    from ehn_basestation.app.bootstrap import create_app
    from ehn_basestation.app.station_link import initialize_station_db

    load_env_file(root / "var" / "pi" / "station.env")
    settings = BasestationSettings.from_env(project_root=root)
    initialize_station_db(settings)
    create_app(settings=settings)
    print("Local-only profile and three user services prepared. Token not printed; hardware not opened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())