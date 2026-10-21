# Standalone MeshCore USB Gateway

This service connects an external USB serial-companion node to the existing
`ehn-gateway-link/1` TCP contract. It is a separate process with no Pi/HAT runtime,
GPIO, sensor, directory-database, or browser-UI dependency. Its code is packaged with
the basestation to reuse the protocol and plaintext policy, not to start another basestation.

The Pi port remains unchanged and usable as a normal MeshCore node. A future independent
HAT sensor collector will feed telemetry to the basestation, which owns destination policy.
Pi-port native UI reuse is future work; this service does not expose that UI yet.

## Install And Start

Install the optional SDK dependency into the project environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[meshcore-usb]"
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev\windows\start-meshcore-usb-gateway.ps1 -SerialPort COM12
```

Alternatively:

```text
python -m ehn_basestation.gateways.meshcore.usb_gateway --serial-port COM12 --port 7465
```

Default endpoint: `127.0.0.1:7465`. Default mode: READ-ONLY. The service reads messages but
does not transmit them, change radio settings, or forward them to the live basestation.
It holds exclusive ownership of the USB device; disconnect the published USB application
first. Stop this process before using another application on the same serial port.

The development server only binds to loopback. It has no remote authentication/TLS and must
not be exposed publicly. Local UI/status clients use cached data, not new serial queries.

## Linux/Pi

Use the same Python package/CLI with a stable device path:

```sh
python3 -m pip install -e '.[meshcore-usb]'
python3 -m ehn_basestation.gateways.meshcore.usb_gateway --serial-port /dev/serial/by-id/<node-id> --port 7465
```

Grant the service user the appropriate serial-device permission. Do not run a second serial
owner, and do not select a port by picking the first connected USB device. The service
reconnects to its configured path; Windows COM-number changes require updating that path.
Linux deployment and physical unplug/replug acceptance still require qualification.

## Controlled Basestation Cutover

Initial standalone testing leaves the live station profile untouched. For a later deliberate
cutover, preserve the current profile, then configure one MeshCore route:

```text
BASESTATION_MESHCORE_LINK_CARRIER=tcp
BASESTATION_MESHCORE_LINK_ENDPOINT=127.0.0.1:7465
BASESTATION_MESHCORE_GATEWAY_ID=meshcore-usb
```

Restart the basestation after changing its profile. The Pi/HAT can remain powered as a
separate node, but only one gateway should handle the basestation's MeshCore route.
To permit RF sends/settings writes, deliberately restart the USB service with
`--allow-transmit` (or the PowerShell helper's `-AllowTransmit`). No automatic radio-backend
failover is implemented. Restore the saved profile to return to the Pi gateway.

## Behavior And Limits

- One SDK session and serialized command path; SDK version pinned to meshcore 2.3.15.
- Device channel capacity is discovered (40 on the test node), not assumed to be 15.
- Battery values describe the external node, not the Pi/HAT. Contacts/battery refresh every
  60 seconds; channels are loaded at connection and refreshed on reconnect.
- Message retrieval runs independently of TCP status reads. Inbound histories are bounded
  at 250 events; outbound requests at 200; dedupe fingerprints at 500; contacts at 2000.
- Direct sender prefixes resolve only when they identify one cached full public key.
  Ambiguous/unknown prefixes remain unresolved, rather than inventing directory identities.
- Outbound direct messages require a known, unambiguous node contact. Outbound channel
  messages require a configured channel. Text must be nonempty ASCII plaintext <=133 bytes.
- Channel secrets and Bluetooth PINs are not returned through the gateway-link interface.
- Local send acceptance is not RF receipt. Direct ACKs update the correlated history entry;
  group sends have no independent recipient receipt. ACKs are not relabeled as delivered.
- The workbench MeshCore panel displays the current node name and offers `Advert Zero Hop`
  when supported, connected, and transmit-enabled. The SDK is called with `flood=False`;
  the operation cannot select flooding. It helps neighboring nodes discover this node, but
  local acceptance does not guarantee remote reception or automatically verify a directory user.
- Repeated delivery IDs return cached status without another send; conflicting content is
  rejected. Idempotency is in-memory and bounded, not durable across service restarts.
- Uncertain send outcomes are reported as indeterminate and are not automatically resent
  by this service. The caller/operator must reconcile them before attempting a new delivery.
- This service does not implement directory routing, telemetry distribution, radio-to-radio
  group bridging, or native Pi-port UI hosting.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_meshcore_usb_gateway.py -v
```

Initial hardware check on 2026-10-04: COM12, Heltec V4.3 OLED, firmware v1.17.1-d929643,
identity EHN-nnj, Public channel 0/private-test channel 1, eight contacts, 40 channel slots.
Read-only broker startup and clean shutdown passed with no RF sends or settings writes.
Automated tests cover gateway-link compatibility, read-only gating, plaintext/target checks,
ACK ordering, delivery-ID conflict/idempotency, ambiguity, bounded history, reconnect,
uncertain outcomes, protocol rejection, and frame-size limits.
Live group/direct RF receipt, physical disconnect/reconnect, Linux operation, and basestation
cutover are separate pending acceptance gates; none is certified by the read-only check.