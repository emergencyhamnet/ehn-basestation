# EHN Basestation Operator Guide

**Audience:** operators of the October 2026 local-directory alpha.

For which profile files, addresses, ports, and USB paths to edit, see the
[Configuration Guide](CONFIGURATION_GUIDE.md). Installation procedures are in
[the Pi installation guide](dev/linux/README.md) and [the project README](README.md).
For Pi-specific settings for both USB radio gateways and the ADB-linked phone, see the
[Raspberry Pi Configuration Guide](PI_CONFIGURATION.md).

This guide describes the current working setup, not a release certification. The fresh Pi SD,
the previous Pi SD and the Windows development installation are separate. Keep them that way
until a deliberate, backed-up cutover.

## System Overview

The basestation owns identity, classification, messaging-mode policy, routing, durable delivery
and audit. Gateways provide transport access; they do not share a radio or USB connection.

```text
Android phone --USB/ADB reverse--> Pi basestation + bundled Operator Workbench
                                      |-- Meshtastic USB node
                                      |-- external MeshCore USB companion via gateway service
                                      `-- local SQLite directory and delivery queue

Separate: Pi-port/HAT MeshCore runtime and its native UI
Not connected in this alpha: Windows Main Directory
```

| Component | Operator role | Fresh Pi address/state |
|---|---|---|
| Basestation + Workbench | Local routing, policy, queues, local directory, Android API and operator UI | `http://<pi-LAN-address>:18098/` |
| External MeshCore gateway | Owns the Heltec companion serial port and exposes gateway-link API | Loopback `127.0.0.1:7465` |
| Meshtastic gateway | Basestation-owned serial broker for RAK4631 | Stable USB by-id path; group channel index `1` |
| Android SMS/RCS | Captures phone SMS/RCS, polls outbound SMS, sends and reports completion | ADB reverse phone `127.0.0.1:18098` to Pi port `18098` |
| Local directory | Station-local users, identities, policies and endpoints | Pi-local SQLite under `var/stations/local/` |
| Main Directory | Not linked to this island alpha | No authority network connection |
| Pi-port/HAT MeshCore | Separate normal node and native UI | Not installed on the fresh Pi SD |

Use a supported 64-bit Raspberry Pi OS installation and confirm the Pi's current LAN address
on its network. Connect one external MeshCore USB companion and one Meshtastic USB radio; use
their stable `/dev/serial/by-id/` paths rather than changing `/dev/ttyACM*` numbers. The radios
must be separate devices with one process owner each.

## Delivery Evidence

Never conflate these outcomes:

1. **Accepted locally:** app/gateway accepted the operation.
2. **Radio ACK/response:** transport-level acknowledgement, where available.
3. **Recipient receipt:** a person confirms seeing or replying to it.

The SMS queue's `delivered` state means the Android app reported SmsManager completion; it is
not a carrier delivery receipt. Mesh radio local acceptance is not proof of RF reception. A
zero-hop advert does not guarantee that a neighbor received it.

## Start, Stop And Status

The Pi uses these user services:

```bash
systemctl --user status ehn-basestation ehn-meshcore-usb ehn-android-usb --no-pager
```

Meshtastic runs inside the basestation service; it has no separate service. Restart only the
affected process where possible:

```bash
systemctl --user restart ehn-basestation
systemctl --user restart ehn-meshcore-usb
systemctl --user restart ehn-android-usb
```

Logs:

```bash
journalctl --user -u ehn-basestation -n 100 --no-pager
journalctl --user -u ehn-meshcore-usb -n 100 --no-pager
journalctl --user -u ehn-android-usb -n 100 --no-pager
```

Useful read-only checks:

```bash
curl -fsS http://127.0.0.1:18098/api/runtime
adb devices -l
adb reverse --list
ls -l /dev/serial/by-id/
```

The UI pages are **Messages**, **MeshCore Gateway**, **Meshtastic Gateway**, **SMS Gateway**,
**Directory**, and **Basestation**. Check each transport's own status and activity. The
Workbench is bundled with the basestation process; there is no separate UI service.

### Reboot Startup

User-service lingering is currently **not enabled**. Services run in the current login session,
but unattended reboot startup is not yet verified. An administrator must run this on the Pi
(enter the sudo password in its terminal, never in chat):

```bash
sudo loginctl enable-linger "$USER"
```

Verify `loginctl show-user "$USER" -p Linger` reports `yes`, then schedule a reboot test and confirm
all services and radios recover. Until that test passes, do not assume the station auto-starts
after power loss.

## Radio And Phone Setup

Provision radio channels, keys and region-compliant settings in the devices' native applications.
The basestation does not configure radio settings. Confirm receivers are powered and listening
on the intended channel/key. Only one process may own each USB radio.

The MeshCore gateway uses the external USB node. Its cached battery reading belongs to that node,
not the Pi/HAT. Live Pi/HAT battery/environmental acquisition is not implemented here.

For Android, the user service maintains ADB reverse mapping `tcp:18098` to `tcp:18098` while the
authorized phone is present. The app must use **USB development (ADB)** and port `18098`; grant
SMS receive/send permissions and notification access if Google Messages RCS capture is required.
ADB debugging authorization is distinct from the basestation bearer token. ADB does not provide
cellular service; carrier SMS still requires a working SIM/network, and RCS has its own network
requirements.

The Pi generates a new token in `~/ehn-basestation/var/pi/station.env`. To view it for direct
entry into the phone, run this only in a trusted Pi terminal. Do not paste the result into chat,
screenshots, tickets or shared logs:

```bash
sed -n 's/^BASESTATION_SMS_ANDROID_AUTH_TOKEN=//p' ~/ehn-basestation/var/pi/station.env
```

Set the token in EHN Relay Link Settings, save, press **Test Link**, then **Start**. Keep the
token private; the profile is mode `600` under a mode-`700` directory. If rotating it, update the
phone and profile together, then restart the basestation and Android tunnel service.

## Directory And Identity

The fresh Pi uses a local directory only. Its configured “authority” lookup path points to the
same local database. The Main Directory/authority console is not connected; local edits do not
submit to or promote users in Main Directory.

- **User ID:** canonical target used after `@`.
- **Identity Label:** EHN routing/display identity.
- **Identity Type:** e.g. `USER`, `DEVICE`, `STATION`.
- **Display Name:** readable person/device name.
- **Gateway endpoint:** actual transport address: SMS phone number, MeshCore public key or
  Meshtastic `!` node ID.
- **RCS Visible Name:** exact Google Messages sender string when notification-derived RCS has
  no sender number. It is not a phone number or SMS endpoint.
- **Messaging Mode:** `DIRECT`, `GROUP`, `BOTH`.

Direct user target names such as `@steve` are matched to EHN identity labels without regard to
letter case. The displayed identity spelling is preserved; only lookup is case-insensitive.

Unknown senders who send valid addressed traffic may receive a local provisional record in
`observed` status. Its endpoint state is `Observed` while disabled, `Enabled` after local opt-in,
and `Verified` after an operator verifies the association; verification also enables the endpoint.
The inbound message continues through normal destination routing. An enabled, locally observed
Meshtastic endpoint can receive direct messages while its user remains observed, subject to
messaging-mode rules; this does not add the user to group fanout. For SMS group fanout, both
`active` and `observed` users are eligible when their mode is `GROUP`/`BOTH` and they have an
enabled SMS endpoint. `inactive` users remain excluded. Discovery alone does not enable the
endpoint. The picker shows `Observed`, the advertised radio name when available, and the node
address, not the generated record ID as an EHN identity. Main standing is separate (`Not
submitted`, `Pending Main`, or `Central`); local-to-Main
submission is not implemented in this alpha. A plain `register` does not create a provisional
record. When an operator assigns an EHN identity to an observed user, that identity appears in
the Directory list and details even though the record retains its original provisional creation
policy. Name-only
RCS observations go into RCS Visible Name, without a fabricated SMS address. The editor and
router also repair the exact legacy observed record on its next matching RCS message.
MeshCore and Meshtastic advertised names are stored as provisional users' display names while
the public key or node ID remains the transport endpoint. The radio name is useful metadata,
not cryptographic proof of an EHN identity. Existing generic observed-radio placeholders can
be enriched when a later inbound message or refreshed MeshCore contact resolves to that exact
endpoint; refreshing the MeshCore Contacts/Nodes view reconciles the directory row even if the
original inbound event is no longer cached. The old message is not routed or sent again.
Trusted/promoted records are not automatically renamed. A MeshCore direct-message packet carries only a short public-key prefix,
not the sender name. The USB gateway refreshes its contact cache when it receives an advert;
then it can resolve the prefix and enrich recent inbound events. If no matching advert/contact
has reached the gateway, the name cannot be inferred from the message alone.
Meshtastic loads the radio's stored node directory on USB connection and uses its cached long
name, falling back to the short name, when a text packet omits user metadata. Refreshing the
Meshtastic gateway view also enriches matching generic observed directory records without
resending traffic. If the radio has not learned a sender's node name, it remains unknown until
node information arrives. Neither network's advertised name promotes or verifies a user.

Group SMS copies go only to active members with `GROUP` or `BOTH` mode and an enabled SMS
endpoint; the originating sender is excluded. “Recently Heard Users” is activity, not the full
Directory list. Directory changes and observed activity can take a short time to appear.

## Message Syntax

Use one address token, a space, then the message body. Keep radio-bound text nonempty ASCII.

| Message | Meaning |
|---|---|
| `@<user-id> <text>` | Direct message to a directory user through its selected/reachable route. |
| `& <text>` | This station's local group. |
| `&NNJ <text>` | Named local alias `NNJ`; other stations do not route until station routing exists. |
| `#<sink-id> <payload>` | Submission to a configured sink; `#` is not group syntax. |
| `register` | Presence/identity lookup; known sender gets `registered`. |

The old `&EHN-NNJ` syntax is retired. `@nobody <text>` is not delivered to a guessed address.
Known direct targets with no available route may be queued and acknowledged `message stored`;
delivery depends on later route recovery/presence or maintenance.

### Direct-message procedure

1. Check exact User ID, enabled endpoint and `DIRECT`/`BOTH` mode in the local Directory.
2. Send `@<user-id> <short unique test>` from a mapped phone or radio.
3. Check sender resolution, destination route and gateway result in Messages/Gateway activity.
4. Confirm receipt at the target or obtain a reply. Record local acceptance, ACK and human receipt
   separately.

An unknown addressed sender can be provisionally recorded with the transport endpoint observed.
On-air names do not merge identities: MeshCore identity resolution uses public keys, not a node
name such as `EHN-nnj`.

### Group-message procedure

1. Verify sender group permission and recipient active status, mode and endpoints.
2. Send `& <short unique test>` from SMS/RCS to test ingress, or use a radio's group channel.
3. Inspect per-transport group results in the SMS Gateway timeline and each radio's activity.
4. Ask a receiver to confirm the message; gateway acceptance alone is not recipient receipt.

SMS/RCS local-group ingress independently attempts SMS group copies, MeshCore's first active
private channel and configured Meshtastic channel index `1`. A failure on one transport does
not block attempts on the others. SMS copies exclude the sender. Inbound MeshCore private-group
and Meshtastic configured-channel messages fan out to eligible SMS members and the other radio's
configured group channel. Radio copies are prefixed with the sender's assigned EHN identity or
advertised node name; if neither can be resolved, they identify the source network and unknown
sender. MeshCore group packets may carry only a `radio name: message` string; a unique cached
contact name can resolve its key to the assigned EHN identity. Ambiguous names remain unknown.
This is display attribution, not authenticated sender identity or an authorization rule.
Disconnected radio group sends are reported failed, not stored for automatic
retransmission. Echo suppression is bounded to 200 in-memory transport/channel/text entries for
five minutes; it resets on service restart.

### MeshCore zero-hop advert

In **MeshCore Gateway**, the header shows the node's MeshCore name. **Advert Zero Hop** requests
the SDK's non-flooding advert command, subject to connected/transmit-enabled state. It helps
nearby nodes discover the sender; it does not guarantee reception or create a directory identity.
Check subsequent observed activity separately. Avoid repeated adverts when an outcome is unclear.

### Meshtastic panel

Use **Meshtastic Gateway** for link/channel status, directory peers, observed nodes, messages and
logs. The alpha's configured group channel is index `1`. Direct sends require a directory
Meshtastic endpoint. Provision keys/channels using the official app; matching channel names do
not prove matching encryption keys. The panel distinguishes directory identity/type from radio
display name/address; unresolved nodes are not assigned an EHN identity.

## Security, Data And Recovery

This is a trusted-LAN alpha: the Workbench listens on port `18098` without TLS or operator login.
Do not port-forward it to the Internet. Restrict LAN access with network/host firewalling. The
external MeshCore link is loopback-only on `7465`. ADB reverse is a trusted USB development link,
not production network security. Logs/databases may contain member, delivery or message data;
keep them and backups private.

The fresh installation uses `~/ehn-basestation/var/` and a fresh local directory/queue. It does
not import Windows data or use Main Directory. Back up the Pi profile and its `var/` data before
updates; profile/backups contain the SMS token. No automatic Pi image or rollback installer is
released. The previous SD remains the hardware rollback option.

## Troubleshooting

| Symptom | First checks |
|---|---|
| Workbench unavailable | `systemctl --user status ehn-basestation`; confirm Pi LAN address and port `18098`. |
| MeshCore disconnected | Check `ehn-meshcore-usb`, by-id path, and exclusive USB ownership. |
| Meshtastic disconnected | Check the RAK4631 by-id path and basestation logs; one serial owner only. |
| SMS waiting/offline | `adb devices -l`, `adb reverse --list`, app USB mode/port, then Test Link and bearer token. |
| ADB `unauthorized` | Unlock phone and accept its debugging prompt. |
| ADB `no permissions` | Install Android udev rules and reconnect; don't run gateways as root. |
| Unknown direct target | Check exact local User ID/identity/endpoints; no name guessing. |
| Direct message stored | Known user has no currently reachable eligible route; inspect delivery queue and expiry. |
| Group recipient missing | Check `GROUP`/`BOTH`, active status, enabled endpoint and that transport's send result. |
| Message accepted but unseen | Check receiver power, channel/key, range; acceptance is not RF ACK or recipient receipt. |
| RCS name in SMS Address | Refresh/edit Directory; put visible name in RCS Visible Name, not an SMS phone endpoint. |

## Acceptance Status And Limits

The operator reports sampled manual path types passing, but not every device-to-device pair;
response and directory-refresh latency remain an open issue. The fresh Pi package/profile and
user services are installed, and current gateway/ADB connections have been observed. Still
required: enter/test the Pi token on the phone after reboot, enable user lingering and verify
startup without login, reboot/reconnect recovery, validate each real direct/group recipient path,
no-internet operation, and record human receipt separately. No Main Directory access, telemetry
fanout, verified-sender-only gate, email fallback, general radio-to-radio group relay, or released
Pi image is claimed.