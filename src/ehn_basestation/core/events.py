from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class MessageEvent:
    event_type: str
    direction: str
    timestamp: str
    gateway_id: str | None = None
    delivery_id: str | None = None
    status: str | None = None
    summary: str | None = None
    network: str | None = None
    sender_endpoint: str | None = None
    sender_user_id: str | None = None
    conversation_scope: str | None = None
    target_id: str | None = None
    actor_class: str | None = None
    content_class: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class MessageEventStore:
    def __init__(self) -> None:
        self._events: list[MessageEvent] = []

    def record(self, event: MessageEvent) -> MessageEvent:
        self._events.append(event)
        return event

    def list_events(self) -> list[dict[str, str | None]]:
        return [event.to_dict() for event in self._events]
