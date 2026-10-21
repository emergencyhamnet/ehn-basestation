from __future__ import annotations

import ipaddress
import argparse
import logging
import socketserver
from typing import Any, Protocol

from ehn_basestation.gateways.meshcore.gateway_link import (
    GatewayLinkEnvelope,
    decode_gateway_link_frame,
    encode_gateway_link_frame,
)


PROTOCOL_VERSION = "ehn-gateway-link/1"
MAX_FRAME_BYTES = 1_048_576


class UsbGatewayBroker(Protocol):
    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


class _GatewayHandler(socketserver.BaseRequestHandler):
    def _read_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.request.recv(size - len(result))
            if not chunk:
                raise ConnectionError("Gateway request ended before its frame completed.")
            result.extend(chunk)
        return bytes(result)

    def handle(self) -> None:
        self.request.settimeout(5.0)
        request_id = None
        try:
            header = self._read_exact(4)
            size = int.from_bytes(header, "big")
            if not 0 < size <= MAX_FRAME_BYTES:
                raise ValueError("Gateway request frame size is invalid.")
            envelope = decode_gateway_link_frame(header + self._read_exact(size))
            request_id = envelope.id
            if envelope.version != PROTOCOL_VERSION or envelope.type != "request" or not envelope.id or not envelope.method:
                raise ValueError("Unsupported gateway request envelope.")
            result = self.server.broker.dispatch(envelope.method, envelope.params or {})
            response = GatewayLinkEnvelope(version=PROTOCOL_VERSION, type="response", id=request_id, ok=True, result=result)
        except Exception as exc:
            response = GatewayLinkEnvelope(
                version=PROTOCOL_VERSION, type="response", id=request_id, ok=False,
                error={"message": str(exc)},
            )
        try:
            self.request.sendall(encode_gateway_link_frame(response))
        except OSError:
            return


class MeshCoreUsbGatewayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    broker: UsbGatewayBroker

    def __init__(self, address: tuple[str, int], broker: UsbGatewayBroker) -> None:
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError("The development USB gateway must bind to a loopback address.")
        self.broker = broker
        super().__init__(address, _GatewayHandler)


def main() -> int:
    from ehn_basestation.gateways.meshcore.usb_broker import MeshCoreUsbBroker

    parser = argparse.ArgumentParser(description="Standalone external MeshCore USB gateway (no Pi/HAT dependency).")
    parser.add_argument("--serial-port", required=True)
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7465)
    parser.add_argument("--gateway-id", default="meshcore-usb")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--allow-transmit", action="store_true")
    args = parser.parse_args()
    if not ipaddress.ip_address(args.host).is_loopback:
        parser.error("Only loopback bindings are supported by this development service.")
    logging.basicConfig(level=logging.WARNING)
    broker = MeshCoreUsbBroker(
        serial_port=args.serial_port, gateway_id=args.gateway_id, baudrate=args.baudrate,
        timeout_seconds=args.timeout, allow_transmit=args.allow_transmit,
    )
    with MeshCoreUsbGatewayServer((args.host, args.port), broker) as server:
        broker.start()
        print(f"MeshCore USB gateway listening on {args.host}:{args.port}; serial {args.serial_port}; transmit {'enabled' if args.allow_transmit else 'disabled'}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            broker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())