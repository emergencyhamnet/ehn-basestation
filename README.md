# EHN Basestation

Transport-neutral basestation for the Emergency Ham Network (EHN).

The basestation owns identity, routing, policy, durable delivery, and audit. Gateways are thin
adapters: a MeshCore node on a Raspberry Pi (`meshcore-pi-port`), an Android phone running the
EHN SMS gateway app, and later Meshtastic. Canonical architecture and specifications live in the
separate `ehn-docs` repository; this README covers installing and running this code.

Status: **alpha**. Pure Python 3.11+, standard library only, SQLite storage. Windows is the
current development and alpha host; nothing here is Windows-specific except the helper scripts
under `dev/windows`.

## What runs where

| Surface | Default | Role |
|---|---|---|
| Workbench (field station) | `http://<host>:18098` | Operator UI, Messages, gateways, local Directory, Basestation panel, Android SMS API |
| Authority console | `http://127.0.0.1:8105` | Main directory: add, edit, review, promote, delete members; redacted queue view |
| MeshCore gateway link | `tcp://<pi>:7463` | Provided by `meshcore-pi-port` on the Pi; this repo is a client of it |
| Android SMS app | polls `http://<host>:18098` | Provided by the EHN Android SMS gateway; this repo serves its API |

## Install

```powershell
git clone https://github.com/emergencyhamnet/ehn-basestation.git C:\ehn\ehn-basestation
cd C:\ehn\ehn-basestation
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Then create your station profiles from the templates and edit the paths and addresses:

```powershell
Copy-Item dev\windows\env\local-station.env.example      dev\windows\env\local-station.env
Copy-Item dev\windows\env\authority-station.env.example  dev\windows\env\authority-station.env
```

Minimum values to change in `local-station.env`:

1. the five `...PATH` / `...ROOT` entries to your install location
2. `BASESTATION_MESHCORE_LINK_ENDPOINT` to your Pi's address, port 7463
3. `BASESTATION_SMS_GATEWAY_ID` to your gateway id, e.g. `EHN-NNJ` (this is also the `&group-id` your users will type)
4. `BASESTATION_SMS_ANDROID_AUTH_TOKEN` to a secret of your own; configure the same token in the Android app

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
loopback. Run exactly one process per port; duplicate listeners produce confusing mixed state.

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
| `&<gateway-id> hello all` | appears on the gateway's private MeshCore channel |
| `#test-sink 21.4C 68%` | a line appended to `var/stations/local/delivery/sinks/test-sink.jsonl` |
| `@nobody hello` | `unknown user`, nothing queued |
| `hello` | no reply; Messages shows `unaddressed_text` |

## Message grammar

Every external message starts with one address token; everything after the first space is the body.

| Form | Meaning |
|---|---|
| `@user-id text` | direct to one user |
| `&group-id text` | the gateway's group (private mesh channel) |
| `#sink-id text` | sensor or device submission to a configured sink |
| `register` | lookup-only registration check |

Unknown senders who send valid addressed traffic get a provisional local record
(`user-<hex>`, pending review) and their message is delivered; on the air they appear by RCS
name or phone number until an operator promotes the record. `register` alone never creates a
record. Full rules: `message-grammar.md` in `ehn-docs`.

## Store and forward

Messages to known users with no reachable route are stored durably and acknowledged with
`message stored`. They are delivered when the user becomes reachable: on any inbound message or
`register` from them, on a MeshCore advert from their node, or by the maintenance tick, which
runs every `EHN_BASESTATION_MAINTENANCE_INTERVAL_SECONDS` (default 60) and also expires stale
rows, releases dead claims, and notifies the sender with `delivery expired`.

## Layout

```text
src/ehn_basestation/
  app/         settings, bootstrap, CLI, workbench and authority HTTP servers
  core/        message policy (grammar), router, presence, delivery queue, maintenance, sinks, templates
  directory/   SQLite stores: directory, station registry, delivery queue
  gateways/    meshcore/, sms/, meshtastic/ (placeholder), registry
  sinks/       sink delivery adapters: local-file, http
ui/static/     workbench and authority console front ends
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

## Alpha limitations

1. Meshtastic adapter is a placeholder; the old prototype is reference only.
2. Authority to field synchronization is on-demand hydration plus manual bundles; no background pull yet.
3. No email fallback route yet.
4. Promoting a provisional user is a manual edit or export/import, not a one-click merge.
5. Closed-network assumption: shared bearer token, no TLS, plaintext bodies in the delivery DB.
