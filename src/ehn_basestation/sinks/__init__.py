"""Sink delivery adapters.

Sinks are onward delivery targets for `#sink-id` submissions. Adapters here only move an
already-accepted, already-stored submission to its configured destination; validation,
queueing, and retry policy stay in the basestation core.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import json
import urllib.error
import urllib.request

from ehn_basestation.directory.delivery_store import SinkEndpointRecord


@dataclass(frozen=True, slots=True)
class SinkDeliveryItem:
    message_id: str
    sink_id: str
    sender_user_id: str
    content_class: str
    body: str
    created_at: str

    def as_record(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sink_id": self.sink_id,
            "sender_user_id": self.sender_user_id,
            "content_class": self.content_class,
            "body": self.body,
            "created_at": self.created_at,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SinkDeliveryOutcome:
    ok: bool
    detail: str = ""
    provider_message_id: str | None = None
    retryable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "provider_message_id": self.provider_message_id,
            "retryable": self.retryable,
        }


class SinkAdapter(Protocol):
    adapter_type: str

    def deliver(self, endpoint: SinkEndpointRecord, item: SinkDeliveryItem) -> SinkDeliveryOutcome: ...


class LocalFileSinkAdapter:
    """Appends one JSON line per submission to a configured file. Config: {"path": "<file>"}."""

    adapter_type = "local-file"

    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None

    def deliver(self, endpoint: SinkEndpointRecord, item: SinkDeliveryItem) -> SinkDeliveryOutcome:
        raw_path = str(endpoint.config.get("path") or "").strip()
        if not raw_path:
            return SinkDeliveryOutcome(ok=False, detail="local-file sink endpoint has no path", retryable=False)
        path = Path(raw_path)
        if not path.is_absolute() and self._base_dir is not None:
            path = self._base_dir / path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item.as_record(), sort_keys=True) + "\n")
        except OSError as exc:
            return SinkDeliveryOutcome(ok=False, detail=f"write failed: {exc}", retryable=True)
        return SinkDeliveryOutcome(ok=True, detail=str(path), provider_message_id=item.message_id)


class HttpSinkAdapter:
    """POSTs the submission as JSON. Config: {"url": ..., "headers": {...}, "timeout_seconds": 10}."""

    adapter_type = "http"

    def __init__(self, *, opener: Any | None = None) -> None:
        self._opener = opener or urllib.request.urlopen

    def deliver(self, endpoint: SinkEndpointRecord, item: SinkDeliveryItem) -> SinkDeliveryOutcome:
        url = str(endpoint.config.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return SinkDeliveryOutcome(ok=False, detail="http sink endpoint needs an http(s) url", retryable=False)
        headers = {"Content-Type": "application/json"}
        configured_headers = endpoint.config.get("headers")
        if isinstance(configured_headers, dict):
            headers.update({str(key): str(value) for key, value in configured_headers.items()})
        timeout = float(endpoint.config.get("timeout_seconds") or 10)
        request = urllib.request.Request(
            url,
            data=json.dumps(item.as_record(), sort_keys=True).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                provider_id = response.headers.get("X-Request-Id") if hasattr(response, "headers") else None
        except urllib.error.HTTPError as exc:
            # 4xx means the collector rejected this submission; retrying will not help.
            return SinkDeliveryOutcome(ok=False, detail=f"http {exc.code}", retryable=exc.code >= 500)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return SinkDeliveryOutcome(ok=False, detail=f"http unreachable: {exc}", retryable=True)
        if status >= 400:
            return SinkDeliveryOutcome(ok=False, detail=f"http {status}", retryable=status >= 500)
        return SinkDeliveryOutcome(ok=True, detail=f"http {status}", provider_message_id=str(provider_id or "").strip() or None)


def default_sink_adapters(*, base_dir: Path | None = None) -> dict[str, SinkAdapter]:
    adapters: list[SinkAdapter] = [LocalFileSinkAdapter(base_dir=base_dir), HttpSinkAdapter()]
    return {adapter.adapter_type: adapter for adapter in adapters}


__all__ = [
    "HttpSinkAdapter",
    "LocalFileSinkAdapter",
    "SinkAdapter",
    "SinkDeliveryItem",
    "SinkDeliveryOutcome",
    "default_sink_adapters",
]
