from __future__ import annotations

import socket
import threading
import unittest

from ehn_basestation.gateways.meshcore import (
    GatewayLinkEnvelope,
    MeshCoreRuntimeSettings,
    StreamMeshCoreGatewayLink,
    decode_gateway_link_frame,
    encode_gateway_link_frame,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class MeshCoreGatewayLinkTests(unittest.TestCase):
    def test_gateway_link_frame_round_trip(self) -> None:
        envelope = GatewayLinkEnvelope(
            version="ehn-gateway-link/1",
            type="request",
            id="req-1",
            method="get_status",
            params={"verbose": True},
        )

        frame = encode_gateway_link_frame(envelope)
        decoded = decode_gateway_link_frame(frame)

        self.assertEqual(decoded.version, "ehn-gateway-link/1")
        self.assertEqual(decoded.type, "request")
        self.assertEqual(decoded.id, "req-1")
        self.assertEqual(decoded.method, "get_status")
        self.assertTrue(decoded.params["verbose"])

    def test_stream_gateway_link_round_trips_over_tcp(self) -> None:
        port = _free_port()
        requests_seen: list[tuple[str, dict | None]] = []

        def serve() -> None:
            with socket.create_server(("127.0.0.1", port), reuse_port=False) as server:
                for _ in range(5):
                    connection, _ = server.accept()
                    with connection:
                        header = _recv_exact(connection, 4)
                        payload_length = int.from_bytes(header, byteorder="big", signed=False)
                        payload = _recv_exact(connection, payload_length)
                        request_envelope = decode_gateway_link_frame(header + payload)
                        requests_seen.append((request_envelope.method or "", request_envelope.params))
                        response = _response_for(request_envelope)
                        connection.sendall(encode_gateway_link_frame(response))

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()

        link = StreamMeshCoreGatewayLink(
            MeshCoreRuntimeSettings(
                gateway_id="meshcore-gateway-1",
                display_name="MeshCore Gateway",
                base_url="",
                contract_version="1.0",
            ),
            carrier="tcp",
            endpoint=f"127.0.0.1:{port}",
        )

        snapshot = link.get_snapshot()
        channels = link.list_channels()
        observed_nodes = link.list_observed_nodes()
        inbound = link.list_gateway_inbound_events(limit=10)
        accepted, message, payload = link.send_directed_private_message(
            recipient_public_key="PUBKEY-ALICE",
            text="hello",
            delivery_id="deliv-1",
        )

        worker.join(timeout=2.0)

        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["status"], "connected")
        self.assertEqual(snapshot["runtime_status"]["link_carrier"], "tcp")
        self.assertEqual(channels[0]["name"], "Primary Public")
        self.assertEqual(observed_nodes[0]["public_key"], "PUBKEY-ALICE")
        self.assertEqual(inbound[0]["event_id"], "evt-1")
        self.assertTrue(accepted)
        self.assertIn("accepted", message.lower())
        self.assertEqual(payload["message_id"], "msg-1")
        self.assertEqual(
            [item[0] for item in requests_seen],
            [
                "get_snapshot",
                "list_channels",
                "list_observed_nodes",
                "list_gateway_inbound_events",
                "send_directed_private_message",
            ],
        )


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining > 0:
        chunk = connection.recv(remaining)
        if not chunk:
            raise RuntimeError("Test server connection closed before frame completed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _response_for(request_envelope: GatewayLinkEnvelope) -> GatewayLinkEnvelope:
    result: dict[str, object]
    if request_envelope.method == "get_snapshot":
        result = {
            "connected": True,
            "status": "connected",
            "gateway_id": "meshcore-gateway-1",
            "display_name": "MeshCore Gateway",
            "identity": {"public_key": "PUBKEY-SELF"},
            "runtime_status": {"interface_mode": "app"},
        }
    elif request_envelope.method == "list_channels":
        result = {"channels": [{"index": 0, "name": "Primary Public"}]}
    elif request_envelope.method == "list_observed_nodes":
        result = {"observed_nodes": [{"node_id": "node-1", "public_key": "PUBKEY-ALICE"}]}
    elif request_envelope.method == "list_gateway_inbound_events":
        result = {"events": [{"event_id": "evt-1", "message_class": "directed_private"}]}
    elif request_envelope.method == "send_directed_private_message":
        result = {
            "accepted": True,
            "message": "Directed private message accepted.",
            "payload": {"status": "sent", "message_id": "msg-1"},
        }
    else:
        return GatewayLinkEnvelope(
            version="ehn-gateway-link/1",
            type="response",
            id=request_envelope.id,
            ok=False,
            error={"message": f"Unsupported test method {request_envelope.method}."},
        )
    return GatewayLinkEnvelope(
        version="ehn-gateway-link/1",
        type="response",
        id=request_envelope.id,
        ok=True,
        result=result,
    )


if __name__ == "__main__":
    unittest.main()