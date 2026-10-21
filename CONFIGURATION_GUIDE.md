# Configuration Guide

This guide explains which files and addresses to change for a local Windows station or the
integrated Raspberry Pi installation. For installation steps, see [the Windows setup](README.md#install)
or [the Pi installation guide](dev/linux/README.md).

For the integrated Pi alpha specifically, see the more focused
[Raspberry Pi Configuration Guide](PI_CONFIGURATION.md).

## Start With The Right File

| Setup | File to edit | How it is created |
|---|---|---|
| Windows field station | `dev/windows/env/local-station.env` | Copy `dev/windows/env/local-station.env.example`; Windows bootstrap also creates it if missing. |
| Windows authority console | `dev/windows/env/authority-station.env` | Copy `dev/windows/env/authority-station.env.example`. Only needed when hosting the authority console. |
| Second Windows field station | `dev/windows/env/field2-station.env` | Copy `dev/windows/env/field2-station.env.example`; it needs its own station ID, databases, and listen port. |
| Integrated Pi station | `var/pi/station.env` | Created by `dev/linux/install-pi.sh`; contains a generated Android token and selected device paths. |

Edit the local `.env` profile, not the `.example` template. Templates are starting points and
are safe to share; active profiles, tokens, databases, logs, and backups are private. Windows
profiles and `var/` are git-ignored. Do not commit or send them to someone helping with setup.
The Pi installer preserves an existing profile; do not copy a profile from another station.

The Windows app reads profile values in [settings.py](src/ehn_basestation/app/settings.py).
The workbench and authority-console listen addresses/ports are command-line options in the
Windows launch scripts, not profile settings.

## Address And Port Map

`127.0.0.1` means this same computer. A service bound to it is not reachable from another
device. `0.0.0.0` means listen on all local IPv4 interfaces; it is a bind address, not the
address to type into a browser. For a LAN client, use the computer's current LAN IP or hostname.
Do not expose the unauthenticated, non-TLS workbench to the public internet.

| Port / address | Purpose | Where to change it |
|---|---|---|
| `18098` | Workbench and Android basestation API. Windows launcher binds `0.0.0.0`; Pi install uses the same port. | Windows: `dev/windows/start-workbench-preview.ps1` or the CLI `--host` / `--port` arguments. Pi: generated `ehn-basestation` user service under `~/.config/systemd/user/`; update the phone/tunnel mapping too if changing the port. |
| `8105` | Windows Authority Console, loopback-only by default. | `dev/windows/start-authority-console.ps1` or its CLI `--host` / `--port` arguments. Keep it on `127.0.0.1` unless you have deliberately secured it. |
| `<pi-ip>:7463` | Network gateway link to the Pi-port MeshCore runtime. | `BASESTATION_MESHCORE_LINK_ENDPOINT` in the field-station profile. The associated Pi web UI commonly uses port `8099` in `BASESTATION_MESHCORE_BASE_URL`. |
| `127.0.0.1:7465` | Loopback gateway link to the separate MeshCore USB companion service, on the same computer. | `BASESTATION_MESHCORE_LINK_ENDPOINT`; the Pi USB service port is set in its generated user service. It is not the Pi-port gateway's `7463`. |
| `7462`, `7464`, `7466` | Station-link/peer replication experiment ports in the example profiles. | `EHN_BASESTATION_LINK_PORT`, `EHN_BASESTATION_PEER_HOST`, and `EHN_BASESTATION_PEER_PORT`. Change only when configuring that station-link topology; keep ports distinct on one host. |
| `COM8` or `/dev/serial/by-id/...` | USB serial device path for Meshtastic or MeshCore. These are device names, not network ports. | `BASESTATION_MESHTASTIC_SERIAL_PORT` in Windows profile; Pi device paths are selected by the installer and referenced by generated services. |

The Android phone in USB/ADB mode uses phone-local `127.0.0.1:18098`, forwarded to the Pi or
Windows host by ADB. In network mode, the phone instead connects to
`http://<host-LAN-IP>:18098`; the host must listen on a LAN interface and the firewall must
allow the connection. The phone and basestation must use the same
`BASESTATION_SMS_ANDROID_AUTH_TOKEN`.

## Common Profile Changes

| Need | Setting / file | Notes |
|---|---|---|
| Set this station's identity | `EHN_BASESTATION_STATION_NAME`, `EHN_BASESTATION_STATION_ID`, `EHN_BASESTATION_STATION_ROLE` | Give each independent station a unique ID. This is especially important when cloning or configuring `field2`. |
| Point at the MeshCore gateway | `BASESTATION_MESHCORE_LINK_CARRIER`, `BASESTATION_MESHCORE_LINK_ENDPOINT`, `BASESTATION_MESHCORE_BASE_URL` | For Pi-port MeshCore, use the Pi's reachable LAN address and gateway port. For the USB companion gateway, use loopback on the same host. Enable with `BASESTATION_MESHCORE_ENABLED`. |
| Select Meshtastic USB radio | `BASESTATION_MESHTASTIC_SERIAL_PORT` | Windows example: `COM8`. Linux: use the stable `/dev/serial/by-id/` path. Set `BASESTATION_MESHTASTIC_ENABLED=1` and check the configured group channel index against the radio. |
| Connect Android SMS/RCS | `BASESTATION_SMS_ENABLED`, `BASESTATION_SMS_ANDROID_AUTH_TOKEN` | Pi installer generates a fresh random token. For a manual profile, use Python `secrets.token_urlsafe(32)`, then enter the same value in EHN Relay Link's **Gateway auth token** field. This bearer token authenticates but does not encrypt HTTP; use USB/ADB or a trusted LAN. Never use `change-me` for a live station. |
| Move local databases or logs | `BASESTATION_DIRECTORY_DB_PATH`, `EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH`, `EHN_BASESTATION_REGISTRY_DB_PATH`, `EHN_BASESTATION_DELIVERY_DB_PATH`, `EHN_BASESTATION_LOG_ROOT` | Parent directories must be writable by the service user. Back up databases before moving them; do not point two live stations at the same delivery database. |
| Change delivery maintenance cadence | `EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS` | `0` disables the periodic maintenance/redispatch worker. |

The example profile files contain further comments and paths:
[local station](dev/windows/env/local-station.env.example),
[authority](dev/windows/env/authority-station.env.example), and
[second field station](dev/windows/env/field2-station.env.example).

## After Editing

1. Check that each address is reachable from the process that uses it. Use the gateway's LAN
   address only when the gateway service is actually listening on that interface; a loopback-only
   service must be on the same host as its client.
2. Restart the affected process. On Windows, stop and rerun its launcher. On Pi, use
   `systemctl --user restart ehn-basestation` for profile changes; restart
   `ehn-meshcore-usb` or `ehn-android-usb` too if their device or port settings changed.
3. Check the gateway status in the Workbench and verify with a low-risk test. Local gateway
   acceptance does not prove that a radio recipient received the message.

For Pi USB path changes or a fresh install, follow [the Pi install procedure](dev/linux/README.md)
and reconcile the generated service configuration there. For daily service operation, see the
[Operator Guide](OPERATOR_GUIDE.md).