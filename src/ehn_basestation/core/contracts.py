from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class DirectedPrivateDeliveryRequest:
    delivery_id: str
    destination: dict[str, Any]
    payload: dict[str, Any]
    source_identity: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryResult:
    delivery_id: str
    gateway_id: str
    network: str
    accepted: bool
    status: str
    detail: str | None = None
    transport_message_id: str | None = None


class GatewayAdapter(Protocol):
    @property
    def gateway_id(self) -> str: ...

    @property
    def supported_networks(self) -> set[str]: ...

    def get_status(self) -> dict[str, Any]: ...

    def list_inbound_events(self, *, limit: int = 50, cursor: str | None = None) -> list[dict[str, Any]]: ...

    def send_directed_private(self, request: DirectedPrivateDeliveryRequest) -> DeliveryResult: ...
