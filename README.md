# EHN Basestation

Transport-neutral basestation for the Emergency Ham Network (EHN).

This repository contains the local alpha basestation, operator UI, and gateway integrations for
MeshCore USB, Meshtastic USB, and Android SMS/RCS. The Android source is in
[`android/sms-gateway`](android/sms-gateway/README.md). The separate Pi-port MeshCore project
and any Main Directory database or private station data are not included. See [LICENSE](LICENSE)
for the MIT license.

The basestation owns identity, routing, policy, durable delivery, and audit. Gateways are adapters:
a MeshCore node on a Raspberry Pi (`meshcore-pi-port`), a USB-serial Meshtastic node, and an
Android phone running the EHN SMS gateway app. Canonical architecture and specifications live in
the separate `ehn-docs` repository; this README covers installing and running this code.

Status: **alpha / formal testing started 2026-10-01**. Python 3.11+ and SQLite. The base
basestation uses the standard library; Meshtastic support is an optional dependency extra.
Windows is the current development and alpha host; nothing here is Windows-specific except the
helper scripts under `dev/windows`.

For day-to-day station operation and the current fresh Pi island setup, see the
[EHN Basestation Operator Guide](OPERATOR_GUIDE.md).
For profile files, gateway addresses, listen ports, and USB device paths, see the
[Configuration Guide](CONFIGURATION_GUIDE.md).
The integrated Pi's MeshCore USB, Meshtastic USB, and Android ADB settings are detailed in the
[Raspberry Pi Configuration Guide](PI_CONFIGURATION.md).

## What runs where

| Surface | Default | Role |
|---|---|---|
| Workbench (field station) | `http://<host>:18098` | Operator UI, Messages, gateways, local Directory, Basestation panel, Android SMS API |
| Authority console | `http://127.0.0.1:8105` | Main directory: add, edit, review, promote, delete members; redacted queue view |
| MeshCore gateway link | `tcp://<pi>:7463` | Provided by `meshcore-pi-port` on the Pi; this repo is a client of it |
| Meshtastic gateway | USB serial (alpha: `COM8`) | One basestation-owned serial broker; group channel index 1; provision with the official Meshtastic client |
| Android SMS app | polls `http://<host>:18098` | Provided by the EHN Android SMS gateway; this repo serves its API |

## Install

Fastest path, from an elevated-or-not PowerShell:

```powershell
git clone https://github.com/emergencyhamnet/ehn-basestation.git C:\ehn\ehn-basestation
cd C:\ehn\ehn-basestation
powershell -ExecutionPolicy Bypass -File dev\windows\bootstrap-windows.ps1 -RunTests
```

The script creates `.venv`, installs the package, writes `dev\windows\env\*.env` from the templates with paths for this checkout, initialises the databases, seeds example members and sinks, runs the tests, and prints the start commands. It never overwrites an existing `.env` or database.

For a station that uses a Meshtastic gateway, add `-WithMeshtastic` to the bootstrap command. This installs the tested Meshtastic client and compatible protobuf version into `.venv`; it does not configure or enable a radio.

Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
# Optional Meshtastic gateway support:
.\.venv\Scripts\python.exe -m pip install -e ".[meshtastic]"
Copy-Item dev\windows\env\local-station.env.example      dev\windows\env\local-station.env
Copy-Item dev\windows\env\authority-station.env.example  dev\windows\env\authority-station.env
```

Minimum values to change in `local-station.env`:

1. the five `...PATH` / `...ROOT` entries to your install location
2. `BASESTATION_MESHCORE_LINK_ENDPOINT` to your Pi's address, port 7463
3. to enable Meshtastic, install with `-WithMeshtastic` and set `BASESTATION_MESHTASTIC_ENABLED=1`, the serial port, and group channel index
4. `BASESTATION_SMS_GATEWAY_ID` to your gateway id, e.g. `EHN-NNJ` (users type `&` locally or `&NNJ` for its named group)
5. `BASESTATION_SMS_ANDROID_AUTH_TOKEN` to a secret of your own; configure the same token in the Android app

## First run

```powershell
$env:PYTHONPATH = "$PWD\src"
$py = ".\.venv\Scripts\python.exe"

# Create var/ layout and schemas
& $py -m ehn_basestation.app.cli --env-file dev\windows\env\local-station.env init-dev-layout
& $py -m ehn_basestation.app.cli --env-file dev\windows\env\authority-station.env init-authority-directory-schema

# Seed example members into the authority DB and example sinks into the local delivery DB
& $py -m ehn_basestation.app.cli --env-file dev\windows\env\authority-station.env seed-directory-users --target authority --input dev\windows\data\example-users.json
& $py -m ehn_basestation.app.cli --env-file dev\windows\env\local-station.env seed-sinks --input dev\windows\data\sinks-seed.json

# Start the authority console and the workbench (two processes)
& $py -m ehn_basestation.app.cli --env-file dev\windows\env\authority-station.env run-authority-console --root . --host 127.0.0.1 --port 8105
& $py -m ehn_basestation.app.cli --env-file dev\windows\env\local-station.env run-workbench-preview --host 0.0.0.0 --port 18098
```

Bind the workbench to `0.0.0.0` so the Android phone can reach it. Keep the authority console on
loopback. The phone must use the host's LAN address, for example
`http://<host-LAN-IP>:18098`; `127.0.0.1` is reachable only from the basestation PC. Run exactly
one process per port; duplicate listeners produce confusing mixed state. One workbench process
must own the Meshtastic serial port.

Replace the example members with real ones in the authority console at `http://127.0.0.1:8105`.
Field stations resolve unknown senders local-first, then against the authority DB, and copy the
match locally.

## Verify

```powershell
& $py -m unittest discover -s tests -p "test_*.py"
```

Then from the phone registered to the Android app, with the workbench Messages tab open:

| Send | Expect |
|---|---|
| `register` | `registered` if the number is in the directory, else `unknown user` |
| `@<user-id> hello` | delivered to that user's best route, or `message stored` if no route is up |
| `& hello all` or `&NNJ hello all` | independently forwards to enabled MeshCore/Meshtastic group channels and queues copies for eligible SMS group members |
| Meshtastic Gateway panel | sends on configured Meshtastic group channel 1 or to a directory peer; acceptance is local gateway acceptance, not proof of RF delivery |
| `#test-sink 21.4C 68%` | a line appended to `var/stations/local/delivery/sinks/test-sink.jsonl` |
| `@nobody hello` | `unknown user`, nothing queued |
| `hello` | no reply; Messages shows `unaddressed_text` |

## Message grammar

Every external message starts with one address token; everything after the first space is the body.

| Form | Meaning |
|---|---|
| `@user-id text` | direct to one user |
| `& text` | this station's group (enabled radio group channels and eligible SMS members) |
| `&NNJ text` | named NNJ group; other station aliases are unavailable until station routing exists |
| `#sink-id text` | sensor or device submission to a configured sink |
| `register` | lookup-only registration check |

Unknown senders who send valid addressed traffic get a provisional local record
(`user-<hex>`, pending review) and their message is delivered; on the air they appear by RCS
name or phone number until an operator promotes the record. `register` alone never creates a
record. Full rules: `message-grammar.md` in `ehn-docs`.

`GROUP` members send and receive group traffic only; `DIRECT` members send and receive direct traffic only; `BOTH` members may do either. The older `&EHN-NNJ` address is retired. Incoming private-channel replies are queued as SMS for active `GROUP`/`BOTH` members with enabled SMS endpoints. Channel events currently lack a sender key, so those SMS messages are labeled `MeshCore group` rather than attributing an unverified person.

Meshtastic channel group text is classified and fanned out to eligible SMS recipients. Group
traffic from SMS/RCS, MeshCore, and Meshtastic can be forwarded independently to enabled
destinations; a failure on one transport does not stop attempts on the others. Radio text must
pass the plaintext/ASCII policy. Disconnected radio group sends are reported as failures, not
stored for automatic retransmission. Radio acceptance is not recipient receipt. A bounded
200-entry, five-minute in-memory guard suppresses duplicate forwarding echoes and resets on
restart.
Recent Mesh Activity defaults to unique text messages, with five visible rows and
up to 20 entries accessible by vertical scrolling. All Activity includes packet/telemetry events
in the same scrollable view. Known directory/radio names appear beside node IDs.
The inventory is radio-observed/live-packet data only;
full NodeDB transfer is disabled to preserve fast serial startup.

Run the focused or complete test suite from a clean checkout before using the system with live
gateways. Do not publish local test logs, station profiles, databases, backups, or recovery
checkpoints; they can contain network details and operational data.

## Store and forward

Messages to known users with no reachable route are stored durably and acknowledged with
`message stored`. They are delivered when the user becomes reachable: on any inbound message or
`register` from them, on a MeshCore advert from their node, or by the maintenance tick, which
runs every `EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS` (default 60) and also expires stale
rows, releases dead claims, and notifies the sender with `delivery expired`.

## A second gateway or a second station

Each station profile is one `.env` file with its own databases, ports, and gateway endpoint.
To run a second field station against a second Pi node, copy
`dev\windows\env\field2-station.env.example` to `field2-station.env`, set its
`BASESTATION_MESHCORE_LINK_ENDPOINT`, and start another workbench on a different port:

```powershell
.\.venv\Scripts\python.exe -m ehn_basestation.app.cli --env-file dev\windows\env\field2-station.env run-workbench-preview --host 0.0.0.0 --port 18099
```

Both stations can share the same authority database. One Android phone can poll only one
workbench, so leave `BASESTATION_SMS_ENABLED=0` on the second station unless it has its own phone.

## Layout

The preferred design for future EHN interfaces and the planned Pi-port admin UI alignment
is recorded in [UI_STYLE.md](UI_STYLE.md). The Pi-port UI has not yet been restyled.

```text
src/ehn_basestation/
  app/         settings, bootstrap, CLI, workbench and authority HTTP servers
  core/        message policy (grammar), router, presence, delivery queue, maintenance, sinks, templates
  directory/   SQLite stores: directory, station registry, delivery queue
  gateways/    meshcore/, meshtastic/ (USB serial broker), sms/, registry
  sinks/       sink delivery adapters: local-file, http
ui/static/     workbench, authority console, shared EHN style sheet, and local logo asset
dev/windows/   env templates, seed data, PowerShell launchers
tests/         unittest suite
```

## Useful CLI commands

```text
init-dev-layout                 create var/ layout and env files
show-settings                   print resolved settings
init-authority-directory-schema create the authority DB
seed-directory-users            load a JSON member bundle (--target local|authority)
show-authority-users            list authority members
export-review-candidates        export provisional field records for authority review
export-directory-seed           export members as a reusable bundle
seed-sinks / show-sinks         configure #sink-id destinations
run-workbench-preview           start the field workbench
run-authority-console           start the authority console
run-peer-link-server / sync-peer-station   station-link replication experiments
```

## Alpha Scope And Limitations

This repository includes the basestation and Workbench, MeshCore USB and Meshtastic USB
gateways, and the Android SMS/RCS phone app. It does not include the separate Pi-port MeshCore
runtime, its native UI, a Main Directory database, or private station data. See the
[MeshCore USB gateway notes](dev/meshcore-usb-gateway.md) for that gateway's setup and limits.

The alpha assumes a trusted local network: the Workbench has no operator login or TLS, and
message bodies are stored in the local delivery database. Do not expose it to the public
internet. Main Directory synchronization, automated identity promotion, email fallback,
hardware sensor telemetry, and a flashable Pi image are not included in this release.
