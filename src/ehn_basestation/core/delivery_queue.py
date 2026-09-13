from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ehn_basestation.core.events import utc_now_iso
from ehn_basestation.directory.delivery_store import DeliveryStore, PendingMessageRecord


@dataclass(frozen=True, slots=True)
class RouteDeliveryWorkItem:
    message_id: str
    route_name: str
    endpoint_network: str
    endpoint_address: str
    plaintext_body: str
    target_user_id: str
    sender_user_id: str
    message_class: str
    conversation_scope: str
    created_at: str
    claim_token: str
    claim_station_id: str

    def as_transport_payload(self) -> dict[str, str]:
        return {
            "message_id": self.message_id,
            "to_number": self.endpoint_address,
            "body": self.plaintext_body,
        }


class DeliveryQueueService:
    def __init__(self, *, delivery_store: DeliveryStore) -> None:
        self._delivery_store = delivery_store

    def list_route_messages(self, *, route_name: str, include_content: bool = False, limit: int = 50) -> list[dict[str, object]]:
        return self._delivery_store.list_pending_messages_for_route(
            route_name=route_name,
            include_content=include_content,
            limit=limit,
        )

    def claim_route_messages(
        self,
        *,
        route_name: str,
        station_id: str,
        limit: int = 10,
        claim_window_seconds: int = 60,
    ) -> list[RouteDeliveryWorkItem]:
        records = self._delivery_store.claim_messages_for_route(
            route_name=route_name,
            station_id=station_id,
            limit=limit,
            claim_window_seconds=claim_window_seconds,
        )
        return [self._to_work_item(route_name=route_name, record=record) for record in records]

    def complete_route_message(
        self,
        *,
        route_name: str,
        station_id: str,
        message_id: str,
        ok: bool,
        provider_message_id: str | None = None,
        detail: str | None = None,
        retryable: bool | None = None,
        retry_delay_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        current = self._delivery_store.get_pending_message(message_id)
        if current is None or current.claim_station_id != str(station_id).strip() or not current.claim_token:
            return None

        completed_at = utc_now_iso()
        if ok:
            updated = self._delivery_store.complete_message(
                message_id=current.message_id,
                station_id=station_id,
                claim_token=current.claim_token,
                outcome="delivered",
                route_name=route_name,
                gateway_request_id=str(provider_message_id or "").strip(),
                outcome_detail=str(detail or ""),
                now=completed_at,
            )
            state = "delivered"
            next_attempt_after = None
        else:
            should_retry = True if retryable is None else bool(retryable)
            if should_retry:
                delay_seconds = max(1, int(retry_delay_seconds or self._retry_delay_seconds(current.target_user_id)))
                next_attempt_after = (self._parse_iso(completed_at) + timedelta(seconds=delay_seconds)).isoformat()
                updated = self._delivery_store.release_message_claim(
                    message_id=current.message_id,
                    station_id=station_id,
                    claim_token=current.claim_token,
                    route_name=route_name,
                    next_attempt_after=next_attempt_after,
                    outcome="retryable_failure",
                    outcome_detail=str(detail or ""),
                    gateway_request_id=str(provider_message_id or "").strip(),
                    now=completed_at,
                )
                state = "retry_scheduled"
            else:
                next_attempt_after = None
                updated = self._delivery_store.complete_message(
                    message_id=current.message_id,
                    station_id=station_id,
                    claim_token=current.claim_token,
                    outcome="failed",
                    route_name=route_name,
                    gateway_request_id=str(provider_message_id or "").strip(),
                    outcome_detail=str(detail or ""),
                    now=completed_at,
                )
                state = "failed"
        if updated is None:
            return None
        return {
            "message_id": updated.message_id,
            "ok": ok,
            "state": state,
            "provider_message_id": str(provider_message_id or "").strip() or None,
            "detail": str(detail or "").strip() or None,
            "next_attempt_after": next_attempt_after,
        }

    def dispatch_route_messages(
        self,
        *,
        route_name: str,
        station_id: str,
        limit: int,
        dispatcher: Any,
        claim_window_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        claimed = self.claim_route_messages(
            route_name=route_name,
            station_id=station_id,
            limit=limit,
            claim_window_seconds=claim_window_seconds,
        )
        results: list[dict[str, Any]] = []
        for item in claimed:
            dispatch_result = dispatcher(item) or {}
            completion = self.complete_route_message(
                route_name=route_name,
                station_id=station_id,
                message_id=item.message_id,
                ok=bool(dispatch_result.get("ok")),
                provider_message_id=str(dispatch_result.get("provider_message_id") or "").strip() or None,
                detail=str(dispatch_result.get("detail") or "").strip() or None,
                retryable=dispatch_result.get("retryable"),
                retry_delay_seconds=dispatch_result.get("retry_delay_seconds"),
            )
            results.append(
                {
                    "message_id": item.message_id,
                    "dispatch_ok": bool(dispatch_result.get("ok")),
                    "completion": completion,
                }
            )
        return results

    def enqueue_route_message(
        self,
        *,
        sender_user_id: str,
        target_user_id: str,
        route_name: str,
        endpoint_network: str,
        endpoint_address: str,
        plaintext_body: str,
        accepted_station_id: str,
        owning_station_id: str,
        message_class: str = "message",
        conversation_scope: str = "single",
        body_summary: str = "",
        created_by_event_id: str = "",
    ) -> PendingMessageRecord:
        return self._delivery_store.enqueue_message(
            sender_user_id=sender_user_id,
            target_user_id=target_user_id,
            target_endpoint_network=endpoint_network,
            target_endpoint_address=endpoint_address,
            conversation_scope=conversation_scope,
            message_class=message_class,
            plaintext_body=plaintext_body,
            accepted_station_id=accepted_station_id,
            owning_station_id=owning_station_id,
            body_summary=body_summary,
            selected_route=route_name,
            created_by_event_id=created_by_event_id,
        )

    def run_maintenance(self, *, station_id: str, now: str | None = None) -> dict[str, Any]:
        """Release stale claims and expire rows past their TTL or attempt budget. Does not dispatch."""
        now_dt = self._parse_iso(now or utc_now_iso())
        released = self._delivery_store.release_expired_claims(now=now_dt.isoformat())
        expired: list[PendingMessageRecord] = []
        for record in self._delivery_store.list_open_messages():
            reason = self._expiry_reason(record, now_dt)
            if reason is None:
                continue
            expired_record = self._delivery_store.expire_message(
                message_id=record.message_id,
                station_id=station_id,
                reason=reason,
                now=now_dt.isoformat(),
            )
            if expired_record is not None:
                expired.append(expired_record)
        return {
            "ran_at": now_dt.isoformat(),
            "released_claims": [record.message_id for record in released],
            "expired": expired,
        }

    def _expiry_reason(self, record: PendingMessageRecord, now_dt: datetime) -> str | None:
        if record.claim_station_id:
            return None
        policy = self._delivery_store.get_delivery_policy(record.target_user_id)
        expires_at = self._parse_optional_iso(record.expires_at)
        if expires_at is None:
            # Default TTL comes from the target's policy, or a day when no policy exists.
            ttl_seconds = int(policy.message_expiry_seconds) if policy is not None and int(policy.message_expiry_seconds) > 0 else 86400
            expires_at = self._parse_iso(record.created_at) + timedelta(seconds=ttl_seconds)
        if expires_at <= now_dt:
            return "ttl_exceeded"
        max_attempts = int(policy.max_attempts) if policy is not None and int(policy.max_attempts) > 0 else 10
        if self._delivery_store.count_delivery_attempts(record.message_id) >= max_attempts:
            return "max_attempts_exceeded"
        return None

    def _parse_optional_iso(self, value: str | None) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    def _to_work_item(self, *, route_name: str, record: PendingMessageRecord) -> RouteDeliveryWorkItem:
        return RouteDeliveryWorkItem(
            message_id=record.message_id,
            route_name=str(route_name).strip().lower(),
            endpoint_network=record.target_endpoint_network,
            endpoint_address=record.target_endpoint_address,
            plaintext_body=record.plaintext_body,
            target_user_id=record.target_user_id,
            sender_user_id=record.sender_user_id,
            message_class=record.message_class,
            conversation_scope=record.conversation_scope,
            created_at=record.created_at,
            claim_token=record.claim_token,
            claim_station_id=record.claim_station_id,
        )

    def _retry_delay_seconds(self, target_user_id: str) -> int:
        normalized_user_id = str(target_user_id or "").strip()
        if normalized_user_id:
            policy = self._delivery_store.get_delivery_policy(normalized_user_id)
            if policy is not None and int(policy.retry_interval_seconds) > 0:
                return int(policy.retry_interval_seconds)
        return 300

    def _parse_iso(self, value: str) -> datetime:
        text = str(value or "").strip()
        if not text:
            return datetime.now(timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))