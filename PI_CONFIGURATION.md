# Raspberry Pi Configuration Guide

This guide covers the integrated Pi alpha: the basestation and Workbench, an external
MeshCore USB companion gateway, an external Meshtastic USB gateway, and an Android phone linked
over USB/ADB for SMS and RCS. The radios and phone remain separate USB devices; the Pi is not
running either radio as a built-in HAT.

For a new installation, follow [the Pi installation procedure](dev/linux/README.md) first. This
guide is for understanding and changing the resulting configuration.

## Configuration Files

| File | Purpose |
|---|---|
| `~/ehn-basestation/var/pi/station.env` | Private station profile: station identity, local database paths, gateway settings, Meshtastic serial path, Android token, and maintenance interval. Created by the installer with restrictive permissions. |
| `~/.config/systemd/user/ehn-basestation.service` | Starts the Workbench on `0.0.0.0:18098`. The Meshtastic broker runs inside this process. |
| `~/.config/systemd/user/ehn-meshcore-usb.service` | Starts the separate MeshCore USB gateway, including its selected serial path and loopback port `7465`. |
| `~/.config/systemd/user/ehn-android-usb.service` | Starts the ADB tunnel helper with the selected phone serial and Workbench port. |
| `dev/linux/configure_pi.py` | Source template that generates the Pi profile and user services. `dev/linux/install-pi.sh` runs it during installation. |

Do not copy `station.env` or generated service files from another Pi: the profile contains a
secret token and station-specific paths/identity. The installer creates a fresh token and local
database for a new installation. Keep the profile private; it should be readable only by the
service user.

## Addresses And Ports

| Address / port | Used for | Configuration guidance |
|---|---|---|
| `0.0.0.0:18098` | Workbench and basestation API listener on the Pi. | Set by the `ehn-basestation` user service, not `station.env`. From another trusted-LAN device, browse to `http://<pi-LAN-address>:18098/`. |
| `127.0.0.1:18098` on the phone | Android app's local endpoint in USB/ADB mode. | The Android USB service forwards this to Pi port `18098`. Keep the app in USB development (ADB) mode; this does not use the Pi's LAN IP. |
| `127.0.0.1:7465` | Local connection from the basestation to the MeshCore USB gateway service. | Set as `BASESTATION_MESHCORE_LINK_ENDPOINT` in `station.env`. The USB service is loopback-only and should remain on the same Pi. This is not the network Pi-port MeshCore gateway on port `7463`. |
| `/dev/serial/by-id/<device>` | Stable Linux path for each USB radio. | The installer selects the MeshCore and Meshtastic devices. The Meshtastic path is in `BASESTATION_MESHTASTIC_SERIAL_PORT`; MeshCore's path and the phone serial are passed to generated service commands. Prefer these stable paths over `/dev/ttyACM*`. |
| `<phone-serial>` | Android device selected by ADB. | The USB helper uses this serial to target the intended phone. Check with `adb devices -l`. |

The Workbench binds to all Pi IPv4 interfaces so trusted-LAN clients can reach it. It has no
operator authentication or TLS in this alpha. Keep it on a trusted network; do not port-forward
it or expose it to the public internet. The MeshCore gateway service is loopback-only and does
not need a LAN firewall opening.

## Common Changes

Edit `~/ehn-basestation/var/pi/station.env` for profile values:

| Need | Setting | Notes |
|---|---|---|
| Identify this station | `EHN_BASESTATION_STATION_NAME`, `EHN_BASESTATION_STATION_ID`, `EHN_BASESTATION_STATION_ROLE` | Use a unique station ID for each independent Pi. The installer default is `pi-island`; change it before running multiple stations or cloning an image. |
| Choose the Meshtastic device | `BASESTATION_MESHTASTIC_SERIAL_PORT` | Set to its `/dev/serial/by-id/` path. The radio's channel and keys are provisioned in the official Meshtastic client, not here. |
| Enable or label gateways | `BASESTATION_MESHCORE_ENABLED`, `BASESTATION_MESHCORE_GATEWAY_ID`, `BASESTATION_MESHCORE_DISPLAY_NAME`, `BASESTATION_MESHTASTIC_ENABLED`, `BASESTATION_MESHTASTIC_GATEWAY_ID`, `BASESTATION_MESHTASTIC_DISPLAY_NAME` | The USB gateway services must also be running and connected to their assigned devices. |
| Choose Meshtastic group channel | `BASESTATION_MESHTASTIC_GROUP_CHANNEL_INDEX` | Must agree with the channel index configured on the radio; the alpha default is `1`. |
| Authenticate the phone | `BASESTATION_SMS_ENABLED`, `BASESTATION_SMS_ANDROID_AUTH_TOKEN` | Installer generates a fresh random token in `station.env`. Enter that exact value in EHN Relay Link's **Gateway auth token** field. Keep it private; it authenticates but does not encrypt HTTP. |
| Change local data locations | `BASESTATION_DIRECTORY_DB_PATH`, `EHN_BASESTATION_AUTHORITY_DIRECTORY_DB_PATH`, `EHN_BASESTATION_DELIVERY_DB_PATH`, `EHN_BASESTATION_REGISTRY_DB_PATH`, `EHN_BASESTATION_LOG_ROOT` | The island setup uses a local directory. Keep databases on persistent Pi storage and back them up before moving them. |
| Change periodic delivery maintenance | `EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS` | Default is 60 seconds; `0` disables the periodic worker. |

The Pi LAN address is assigned by the network, not by `station.env`. If DHCP changes it, use
the new address when opening the Workbench; in USB/ADB mode, the phone still talks through its
local reverse tunnel.

## Changing USB Devices Or Service Ports

The installer records hardware selections in both the profile and generated service commands.
It intentionally rejects a rerun when the selected devices differ from the existing profile.
For a replacement radio or phone, first identify the new stable device path, then update the
relevant profile value and matching generated service `ExecStart` argument together. Do not
change only one side: the basestation could point at a different device than its service owns.
After changing a user service file, reload and restart user services:

```bash
systemctl --user daemon-reload
systemctl --user restart ehn-meshcore-usb ehn-basestation ehn-android-usb
```

Changing the Workbench port requires coordinated changes to the basestation service, Android
USB helper, ADB reverse mapping, and EHN Relay Link's USB port. The alpha defaults to `18098`;
leave it unchanged unless there is a port conflict and you can update every part of that path.

## Verify

After configuration changes, verify the selected devices and services:

```bash
ls -l /dev/serial/by-id/
adb devices -l
systemctl --user status ehn-basestation ehn-meshcore-usb ehn-android-usb --no-pager
adb reverse --list
curl -fsS http://127.0.0.1:18098/api/runtime
```

Then open `http://<pi-LAN-address>:18098/` from a trusted-LAN browser and check both gateway
statuses. A connected status or local gateway acceptance is not proof that a radio recipient
received a message. See the [Operator Guide](OPERATOR_GUIDE.md) for operations and recovery.