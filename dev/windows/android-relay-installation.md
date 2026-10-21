# EHN Relay Link: Installation And Use

Version: 0.2.1-usb-dev (version code 3)
Package: org.emergencyham.basestation.androidgateway
Minimum Android: 9 (API 28). This alpha has been exercised on Android 12; device behavior and
permission prompts vary by manufacturer.

This is a debug-signed alpha development APK, not a production release. It connects the
phone's SMS/RCS edge to an EHN basestation. The basestation owns directory and routing policy.
USB mode uses ADB reverse, not USB tethering or a standalone USB serial protocol.

## Package Contents

- EHN-Relay-Link-0.2.1-usb-dev.apk
- INSTALLATION.md (this guide)
- LICENSE (MIT)
- SHA256SUMS.txt (SHA-256 hashes of all other package files)
- dev/windows/connect-usb.ps1
- dev/windows/capture-usb-logs.ps1

The package does not contain configured credentials, member databases, source backups, or logs.
New installations have no preset network URL, SMS line, or auth token. Supply your own settings.

## Build The APK From This Repository

The source repository does not include a prebuilt APK. Build it on a computer with JDK 17,
Android SDK API 34, and internet access for Gradle dependencies. From the repository root:

```powershell
.\android\sms-gateway\gradlew.bat -p .\android\sms-gateway :app:testDebugUnitTest :app:assembleDebug --no-daemon --console=plain
```

```bash
./android/sms-gateway/gradlew -p android/sms-gateway :app:testDebugUnitTest :app:assembleDebug --no-daemon --console=plain
```

The APK is written to
`android/sms-gateway/app/build/outputs/apk/debug/app-debug.apk`. It is debug-signed for alpha
testing. Keep the SDK's `local.properties`, build outputs, and signing material out of Git.
Detailed Android project requirements are in [the Android source build guide](../../android/sms-gateway/README.md).

## Before Installation

Use a trusted computer, a data-capable USB cable, and Android Platform Tools for ADB.
The basestation must be running, with its Android SMS gateway enabled, an operator-supplied
gateway token, and the required local directory members/endpoints configured.

Extract the ZIP on the host. Compare the APK hash with its entry in SHA256SUMS.txt:

```powershell
Get-FileHash .\EHN-Relay-Link-0.2.1-usb-dev.apk -Algorithm SHA256
```

The checksum detects accidental changes; it is not independent proof of publisher identity.
Obtain both the package and any expected checksum through a trusted channel.

## Sideload The APK

On the phone, open the APK from Files and permit installation from that trusted source when
Android requests it. Revoke that source's installation permission afterward if not needed.

Alternatively, enable Developer options and USB debugging, connect the phone, unlock it,
and accept the computer's debugging authorization. From the extracted package directory:

```powershell
adb devices -l
adb install -r .\EHN-Relay-Link-0.2.1-usb-dev.apk
```

When building from source, replace the APK path with
`android\sms-gateway\app\build\outputs\apk\debug\app-debug.apk` (Windows) or
`android/sms-gateway/app/build/outputs/apk/debug/app-debug.apk` (Linux/macOS). If the phone is
connected to another host, copy the APK to the phone and open it with Files, or run `adb install`
from the host that can see the authorized phone.

ADB must be on PATH. An Android Studio installation normally provides it at
`%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`; otherwise use your Platform Tools path.
If multiple devices are attached, add `-s <serial>` immediately after `adb`.

Open EHN Relay Link. An in-place update of a compatible installation preserves its saved
settings and data. If installation reports a signature conflict, STOP: do not uninstall or
clear data to force the update. Resolve signing compatibility or arrange a safe migration.
The older standalone EHN bridge is a separate app and is not updated by this package.

## Permissions And Settings

Allow SMS receive/send permissions. Allow notifications where requested. For Google Messages
RCS capture, enable EHN Relay Link under Android's Notification access settings. The app does
not need to replace Google Messages as the default SMS app. Android menus vary by device.

In app Settings:

- Enter the exact token configured in `BASESTATION_SMS_ANDROID_AUTH_TOKEN` in the basestation's
	private profile. The app field is named **Gateway auth token**.
- Set the local SMS number to this gateway phone's own SMS line, not a remote member's number.
- Leave poll interval at 15 seconds and poll limit at 10 initially.
- Select the connection mode as described below, then save and use Test Link.

### Shared Gateway Token

The basestation and phone must have the same high-entropy token. The Pi installer generates a
new random token automatically and stores it in `var/pi/station.env`. For a manually configured
station, generate one with Python instead of choosing a password or reusing another station's
token:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Put the generated value in the private basestation profile as
`BASESTATION_SMS_ANDROID_AUTH_TOKEN`, then enter the same value in the app's **Gateway auth
token** field. Keep it out of source files, screenshots, logs, and support chats. The app sends
it in the `Authorization: Bearer` request header.

This token authenticates the app; it does **not** encrypt messages or protect the token in
transit. The alpha uses plain HTTP. Prefer USB/ADB with an authorized host over USB; if using
Network mode, use only a trusted, access-controlled LAN. Do not port-forward the Workbench or
use this HTTP connection on an untrusted/public network.

To rotate the token, set a newly generated value in the basestation profile and app, restart the
basestation, then use **Test Link**. Both sides must be updated; requests using the old value
will be rejected.

### USB Development Mode

Run the basestation on the host, normally port 18098. Create the phone-to-host tunnel:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev\windows\connect-usb.ps1
```

The policy exception applies only to that script process. The script uses Android Studio's
default ADB path; pass `-AdbPath C:\Tools\platform-tools\adb.exe` for another installation.
Use `-DeviceSerial <serial>` when more than one phone is connected. For another basestation
port, add `-Port <port>` and enter that same USB port in the app.

In app Settings select USB development (ADB), with USB basestation port 18098 by default.
The effective URL is phone loopback `http://127.0.0.1:18098`, forwarded to this host by ADB.
The saved network URL and auth token remain intact. USB mode does not fall back to Wi-Fi.
Press Test Link. After it succeeds, press Start to enable normal outbound queue polling.

After unplugging, rebooting, or restarting ADB, rerun the tunnel script and Test Link.
Automatic tunnel reconnect is not implemented. Stop pauses the outbound polling service;
it does not disable Android's SMS receiver or notification-listener permissions.

Linux/Pi hosts with ADB can create the same development tunnel:

```sh
adb -s <serial> reverse tcp:18098 tcp:18098
```

Only authorize trusted hosts. Do not expose ADB over an untrusted network. Production USB
deployment, tethering, and unattended startup/recovery require further qualification.

### Network Mode

Select Network (Wi-Fi / LAN) and enter the reachable basestation URL, for example
`http://<host-address>:18098`. The host must listen on its LAN interface and permit the port
through its firewall. Do not use 127.0.0.1 here unless intentionally using a local tunnel.
Save, press Test Link, then Start. Tokens and SMS content use the existing HTTP protocol;
restrict network mode to the trusted deployment network rather than exposing it publicly.

## Messaging Checks

Send from a directory-mapped member phone to the gateway phone's SMS number:

- `register`: expect the basestation's `registered` reply for a known member.
- `@<identity> <text>`: directed message, subject to directory and messaging-mode policy.
- `& <text>`: local group, subject to group policy and enabled gateway configuration.
- `#<sink-id> <text>`: configured sink submission, not a group message.

Google Messages may deliver the incoming event as notification-derived RCS rather than a
carrier SMS broadcast. RCS names must match the directory's configured RCS name. Outbound
replies use SMS. Check actual recipient receipt separately from the app's completion report.
SmsManager submission/completion reporting is not an independent carrier delivery receipt.

## Remote Debug Logs

Capture a snapshot from the authorized host:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev\windows\capture-usb-logs.ps1
```

Add `-Follow` for live capture; Ctrl+C stops it. Add `-OutputPath <file>` or `-AdbPath <path>`
when needed. Default output is `var/usb-logs/` under the extracted package directory.
Live capture files grow until stopped; snapshots contain only the current Logcat buffer.

Only the EHNRelayLink tag is captured: method, route category, mode, HTTP status, and valid
delivery IDs/phases. Tokens, phone numbers, contact names, and message bodies are excluded.
Status 0 means a connection/request failure, not an HTTP response. HTTP 200 means API
acceptance, not recipient receipt. The on-phone activity log is separate and can contain
contact details; do not export it as though it were the sanitized log.

## FAQ: ADB Says No Permissions On Linux Or Raspberry Pi

**Symptom:** `adb devices -l` lists the phone but reports `no permissions (missing udev
rules? user is in the plugdev group)`. This is host USB access, not a gateway token failure.

On Debian/Raspberry Pi OS, install the Android USB rules and reload them:

```bash
sudo apt install -y adb android-sdk-platform-tools-common
sudo udevadm control --reload-rules
```

Unplug and reconnect the phone after reloading. Installing the package alone may leave the
already-connected device with its old permissions. Unlock the phone and accept the USB
debugging authorization prompt for this host if it appears. Then, as the normal service user:

```bash
adb devices -l
```

- `device`: USB access and debugging authorization are ready.
- `unauthorized`: host access works; accept the phone's USB debugging prompt.
- `no permissions`: check `id -nG` and the installed udev rules; authorization is not yet the issue.
- No device: check USB debugging, data cable, connection, and phone USB mode.

If the rules use `plugdev` and the service user is not a member, an administrator can add
that account with `sudo usermod -aG plugdev <service-user>`; log out/back in before retesting.
Do not solve this by running the gateway/ADB persistently as root or making all USB devices
world-writable. After reconnecting, recreate the ADB reverse tunnel if the host does not
already run the tunnel-recovery service.

Verified on the fresh Debian 13 Pi image: installing `android-sdk-platform-tools-common`,
reloading rules, reconnecting, and authorizing the phone changed ADB status from
`no permissions` to `device` without running ADB as root.

## Troubleshooting And Rollback

- ADB unauthorized: unlock the phone and accept the USB debugging prompt.
- No ADB device: check USB debugging, cable/data support, and Windows device drivers.
- Test Link fails: check the tunnel, matching port, running host server, and token.
- HTTP 401: check the gateway token without exposing it in a screenshot or support log.
- No RCS ingress: check notification access and the directory's RCS-name mapping.
- No SMS send/receive: check permissions, SIM/service, and the expected transport in logs.
- Suspected duplicates: compare delivery IDs and report the evidence; do not blindly resend.

To return to Wi-Fi, select Network mode and Test Link using the preserved network URL.
Remove the development tunnel with `adb -s <serial> reverse --remove tcp:18098` if desired.
The maintainer has preserved the original installed APK/source before USB development.
Restore a compatible backed-up debuggable APK with `adb install -r -d <backup.apk>` only
after arranging rollback with the maintainer; do not clear app data.

## Verification Scope

0.2.1-usb-dev passes the five transport/privacy unit tests and APK build/signature checks.
The preceding 0.2.0-usb-dev build was tested on Android 12: in-place update, USB Test Link,
tunnel failure/recovery, normal USB polling, remote log capture, and RCS registration ingress
with human-confirmed receipt of the SMS reply. 0.2.1 removes inherited installation defaults
and increments the version; it has not replaced that live phone installation during packaging.
Plain carrier-SMS broadcast ingress, broader phone compatibility, production USB operation,
and mesh-recipient delivery still require their own acceptance tests.