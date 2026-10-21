# Fresh Pi 4 Island Installation

For a map of the profile settings, network addresses, ports, and device paths, see the
[Configuration Guide](../../CONFIGURATION_GUIDE.md).
For the integrated MeshCore USB, Meshtastic USB, and Android ADB configuration, see the
[Raspberry Pi Configuration Guide](../../PI_CONFIGURATION.md).

This alpha runs the basestation/workbench UI, local directory, Meshtastic USB broker,
standalone MeshCore USB gateway, and Android ADB USB link. It does NOT install the
Pi-port/HAT MeshCore runtime or a Main Directory server. HAT telemetry is future work.
Keep the old SD card and Windows deployment intact.

Reference target: Pi 4, 64-bit Debian/Raspberry Pi OS 13, Python 3.13, non-root service user.
The deployment is trusted-LAN only: the workbench listens on port 18098 without TLS or
operator authentication. Do not expose it publicly. MeshCore gateway port 7465 is loopback-only.

## Administrator Prerequisites

Run these directly on the Pi; enter sudo passwords there, never in a support chat:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git adb android-sdk-platform-tools-common
sudo udevadm control --reload-rules
sudo loginctl enable-linger <service-user>
```

The service user needs radio serial access (normally `dialout`) and Android USB access
(normally `plugdev`). Group changes require a new login. For ADB `no permissions`, reconnect
the phone after reloading udev rules; see the Linux/Pi FAQ in
[the Android installation guide](../windows/android-relay-installation.md).
Unlock the phone and authorize this Pi for USB debugging. Never run the normal gateway as root.
User services need lingering to start before an interactive login. Run the administrator step
above; if sudo requires a password, enter it directly in the Pi terminal, never in a chat.

## Source And Devices

Use the complete current source bundle, not just a Python wheel: it includes `ui/static/`,
the configuration tools and tests. Do not deploy a Windows `.venv`, `.env`, `var/`, member
databases, private logs or credentials. Extract into a new directory owned by the service user,
for example `~/ehn-basestation`. Installation creates a new virtual environment and new data.

Identify hardware without opening the radios:

```bash
lsusb
ls -l /dev/serial/by-id/
adb devices -l
```

Select the MeshCore and Meshtastic devices by their stable by-id symlinks, not ttyACM numbers.
Use the authorized USB phone's ADB serial. Provision radio settings/channels with their native
applications beforehand, then disconnect those applications so each device has one owner.

## Install As The Service User

From the extracted source directory:

```bash
bash dev/linux/install-pi.sh \
  '/dev/serial/by-id/<MeshCore-device>' \
  '/dev/serial/by-id/<Meshtastic-device>' \
  '<Android-USB-serial>'
```

The installer preserves an existing profile/token. A rerun with different selected devices is
rejected rather than silently replacing configuration. Initial installation needs internet for
Python dependencies; normal local routing must not depend on internet or a Main DB connection.
Installed dependency versions are recorded privately in `var/pi/installed-packages.txt`.

Three user services are enabled:

- `ehn-meshcore-usb`: owns the external node, loopback port 7465, transmit enabled.
- `ehn-basestation`: owns Meshtastic and serves workbench/API on port 18098.
- `ehn-android-usb`: maintains the selected phone's reverse tunnel, checking every five seconds.

The profile `var/pi/station.env` is mode 600, its directory mode 700. Local and authority lookup
paths point to the SAME local directory DB; no separate authority database/member data is copied.
No users are seeded. Create/verify local test users and endpoints in the workbench for acceptance.

## Phone Configuration

Install EHN Relay Link on the Android phone before configuring its connection. Build the APK
from this repository and sideload it by following the
[Android build and installation guide](../windows/android-relay-installation.md). The source build can run
on a separate computer; the phone need not be connected to the Pi during the build. If using
ADB to install, connect the phone to the host running the `adb install` command and authorize
that host on the phone.

After installation, keep EHN Relay Link in USB development (ADB) mode, port 18098. SMS and
notification-listener permissions remain necessary. The Pi generates a fresh token; enter it
directly into the phone's Settings. View it ONLY in a private Pi terminal, and do not paste the
output into chat:

```bash
sed -n 's/^BASESTATION_SMS_ANDROID_AUTH_TOKEN=//p' var/pi/station.env
```

Copy only the value into the phone, save, Test Link, then Start. Network URLs remain preserved
on the phone but are not used in USB mode. The host link does not require Wi-Fi. Carrier SMS
still needs cellular service, and RCS has its own network requirements.

In the app's **Gateway auth token** field, enter the exact value from the Pi profile. The
basestation sends it as a Bearer token and rejects requests with a different value. The Pi
installer generates a fresh random token for a new profile; don't replace it with a shared
example or reuse one from another station. This token authenticates requests but does not
encrypt HTTP. USB/ADB uses the authorized physical USB link; Network mode is plain HTTP and must
be confined to a trusted, access-controlled LAN.

For manual profile creation or token rotation, see the
[shared-token instructions](../windows/android-relay-installation.md#shared-gateway-token).
Update the Pi profile and phone together, restart the basestation, and press **Test Link** again.

## Verification And Recovery

```bash
systemctl --user status ehn-meshcore-usb ehn-basestation ehn-android-usb --no-pager
loginctl show-user "$USER" -p Linger
adb reverse --list
curl -fsS http://127.0.0.1:18098/api/runtime
```

Open `http://<pi-address>:18098/` from a trusted LAN browser. Check actual node identity,
gateway connectivity, channel configuration, directory modes, and recipient receipt separately.
Service status alone is not message-delivery proof. Logs are available with
`journalctl --user -u <service> -n 100`; keep operational logs private.

After changing configuration, restart the affected services:

```bash
systemctl --user restart ehn-meshcore-usb ehn-basestation ehn-android-usb
```

The tunnel service re-establishes a missing mapping when the authorized phone reconnects.
Phone authorization may still require a human prompt. User lingering is required for unattended
boot; without it, services depend on that user's session. Android app restart after phone reboot
is not automated by this installer and must be qualified on the chosen phone.

Acceptance gates: focused Linux tests, every required message path, fresh local identity handling,
Pi reboot/startup without a login, USB unplug/replug, network loss, stale-device indications,
and runtime without internet/Main DB. Record adjustments in these scripts/templates/guide.
Do not publish a Pi image until the fresh-install procedure and recovery gates pass.