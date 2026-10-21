from __future__ import annotations

from dataclasses import dataclass


VALID_ACTOR_CLASSES = {"person", "device"}
DEFAULT_CONTENT_CLASS = "message"
REGISTER_BODY = "register"
TARGET_TYPE_BY_PREFIX = {"@": "user", "&": "group", "#": "sink"}
_IDENTIFIER_EXTRA_CHARS = {"-", "_", "."}


@dataclass(frozen=True, slots=True)
class TransportPolicyProfile:
    network: str
    network_family: str
    requires_explicit_target: bool
    implicit_group_channel_supported: bool
    inbound_plaintext_required: bool
    outbound_ham_plaintext_required: bool
    allowed_content_classes: tuple[str, ...] = ("message", "telemetry")


@dataclass(frozen=True, slots=True)
class InboundTextMessage:
    gateway_id: str
    network: str
    sender_endpoint: str
    text: str
    actor_class: str = "person"
    content_class: str = DEFAULT_CONTENT_CLASS
    is_plaintext: bool = True
    is_ehn_group_channel: bool = False
    channel_name: str | None = None
    # Transport-visible sender name (e.g. RCS contact name); ingress evidence only, never identity.
    sender_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class MessageTarget:
    target_type: str
    target_id: str


@dataclass(frozen=True, slots=True)
class ClassifiedInboundMessage:
    network: str
    gateway_id: str
    sender_endpoint: str
    actor_class: str
    content_class: str
    conversation_scope: str | None
    action: str
    reason: str | None
    is_valid_ehn: bool
    requires_master_lookup: bool
    ham_plaintext_gate_required: bool
    target: MessageTarget | None
    body: str


@dataclass(frozen=True, slots=True)
class HamPlaintextResult:
    accepted: bool
    rendered_text: str | None
    reason: str | None


_DEFAULT_EXTERNAL_POLICY = TransportPolicyProfile(
    network="external",
    network_family="external",
    requires_explicit_target=True,
    implicit_group_channel_supported=False,
    inbound_plaintext_required=False,
    outbound_ham_plaintext_required=False,
)

_TRANSPORT_POLICY_BY_NETWORK: dict[str, TransportPolicyProfile] = {
    "meshcore": TransportPolicyProfile(
        network="meshcore",
        network_family="mesh",
        requires_explicit_target=True,
        implicit_group_channel_supported=True,
        inbound_plaintext_required=False,
        outbound_ham_plaintext_required=True,
    ),
    "meshtastic": TransportPolicyProfile(
        network="meshtastic",
        network_family="mesh",
        requires_explicit_target=True,
        implicit_group_channel_supported=True,
        inbound_plaintext_required=False,
        outbound_ham_plaintext_required=True,
    ),
    "sms": TransportPolicyProfile(
        network="sms",
        network_family="external",
        requires_explicit_target=True,
        implicit_group_channel_supported=False,
        inbound_plaintext_required=False,
        outbound_ham_plaintext_required=False,
    ),
    "aprs": TransportPolicyProfile(
        network="aprs",
        network_family="ham-external",
        requires_explicit_target=True,
        implicit_group_channel_supported=False,
        inbound_plaintext_required=True,
        outbound_ham_plaintext_required=True,
    ),
}


def get_transport_policy(network: str) -> TransportPolicyProfile:
    normalized = str(network or "").strip().lower()
    if not normalized:
        return _DEFAULT_EXTERNAL_POLICY
    return _TRANSPORT_POLICY_BY_NETWORK.get(
        normalized,
        TransportPolicyProfile(
            network=normalized,
            network_family="external",
            requires_explicit_target=True,
            implicit_group_channel_supported=False,
            inbound_plaintext_required=False,
            outbound_ham_plaintext_required=False,
        ),
    )


def classify_inbound_text_message(
    message: InboundTextMessage,
    *,
    policy: TransportPolicyProfile | None = None,
) -> ClassifiedInboundMessage:
    active_policy = policy or get_transport_policy(message.network)
    gateway_id = str(message.gateway_id or "").strip().upper()
    sender_endpoint = str(message.sender_endpoint or "").strip()
    text = str(message.text or "").strip()
    actor_class = str(message.actor_class or "person").strip().lower() or "person"
    content_class = str(message.content_class or DEFAULT_CONTENT_CLASS).strip().lower() or DEFAULT_CONTENT_CLASS

    if actor_class not in VALID_ACTOR_CLASSES:
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason="unsupported_actor_class",
        )

    if content_class not in active_policy.allowed_content_classes:
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason="unsupported_content_class",
        )

    if not text:
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason="empty_message",
        )

    if active_policy.inbound_plaintext_required and not bool(message.is_plaintext):
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason="non_plaintext_source",
        )

    if _has_legacy_ehn_prefix(text):
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason="legacy_prefix",
        )

    implicit_group = bool(message.is_ehn_group_channel) and active_policy.implicit_group_channel_supported

    if _is_register_command(text):
        return ClassifiedInboundMessage(
            network=active_policy.network,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            conversation_scope="group" if implicit_group else "single",
            action="register",
            reason=None,
            is_valid_ehn=True,
            requires_master_lookup=True,
            ham_plaintext_gate_required=active_policy.outbound_ham_plaintext_required,
            target=MessageTarget(target_type="group", target_id=gateway_id),
            body=REGISTER_BODY,
        )

    parts = text.split(None, 1)
    target_token = parts[0]
    body = parts[1] if len(parts) > 1 else ""
    prefix = target_token[:1]
    target_type = TARGET_TYPE_BY_PREFIX.get(prefix)

    if target_type is None:
        if implicit_group:
            return ClassifiedInboundMessage(
                network=active_policy.network,
                gateway_id=gateway_id,
                sender_endpoint=sender_endpoint,
                actor_class=actor_class,
                content_class=content_class,
                conversation_scope="group",
                action="route",
                reason=None,
                is_valid_ehn=True,
                requires_master_lookup=True,
                ham_plaintext_gate_required=active_policy.outbound_ham_plaintext_required,
                target=MessageTarget(target_type="group", target_id=gateway_id),
                body=text,
            )
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason="unaddressed_text" if prefix.isalnum() else "unknown_target_prefix",
        )

    identifier = target_token[1:]
    normalized_body = body.strip()
    if target_type == "group" and not identifier and normalized_body:
        identifier = gateway_id
    if target_type == "group" and identifier.upper() == gateway_id.removeprefix("EHN-"):
        identifier = gateway_id
    if target_type == "group" and target_token[1:].upper() == gateway_id and gateway_id.startswith("EHN-"):
        reason = "retired_group_id"
    elif not identifier:
        reason = "empty_target"
    elif not _is_valid_identifier(identifier):
        reason = "invalid_target"
    elif not normalized_body:
        reason = "missing_body"
    else:
        reason = None
    if reason is not None:
        return _log_only_result(
            message=message,
            policy=active_policy,
            gateway_id=gateway_id,
            sender_endpoint=sender_endpoint,
            actor_class=actor_class,
            content_class=content_class,
            reason=reason,
        )

    if target_type == "group":
        target_id = identifier.upper()
        conversation_scope: str | None = "group"
    elif target_type == "user":
        target_id = identifier.lower()
        conversation_scope = "single"
    else:
        target_id = identifier.lower()
        conversation_scope = None

    return ClassifiedInboundMessage(
        network=active_policy.network,
        gateway_id=gateway_id,
        sender_endpoint=sender_endpoint,
        actor_class=actor_class,
        content_class=content_class,
        conversation_scope=conversation_scope,
        action="route",
        reason=None,
        is_valid_ehn=True,
        requires_master_lookup=True,
        ham_plaintext_gate_required=active_policy.outbound_ham_plaintext_required,
        target=MessageTarget(target_type=target_type, target_id=target_id),
        body=normalized_body,
    )


def _has_legacy_ehn_prefix(text: str) -> bool:
    upper = str(text or "").strip().upper()
    return upper in {"EHN", "EHN:"} or upper.startswith("EHN ")


def _is_valid_identifier(value: str) -> bool:
    return all(ch.isalnum() or ch in _IDENTIFIER_EXTRA_CHARS for ch in value)


def _is_register_command(text: str) -> bool:
    return str(text or "").strip().casefold() == REGISTER_BODY


def prepare_ham_plaintext(*, network: str, text: str, ham_plaintext: str | None = None) -> HamPlaintextResult:
    policy = get_transport_policy(network)
    candidate = str(ham_plaintext if ham_plaintext is not None else text or "").strip()
    if not candidate:
        return HamPlaintextResult(accepted=False, rendered_text=None, reason="missing_text")
    if not policy.outbound_ham_plaintext_required:
        return HamPlaintextResult(accepted=True, rendered_text=candidate, reason=None)
    if not _is_ham_plaintext_compliant(candidate):
        return HamPlaintextResult(accepted=False, rendered_text=None, reason="non_plaintext_payload")
    return HamPlaintextResult(accepted=True, rendered_text=candidate, reason=None)


def _is_ham_plaintext_compliant(text: str) -> bool:
    for char in str(text):
        codepoint = ord(char)
        if char in {"\r", "\n", "\t"}:
            continue
        if codepoint < 32 or codepoint > 126:
            return False
    return True


def _log_only_result(
    *,
    message: InboundTextMessage,
    policy: TransportPolicyProfile,
    gateway_id: str,
    sender_endpoint: str,
    actor_class: str,
    content_class: str,
    reason: str,
) -> ClassifiedInboundMessage:
    return ClassifiedInboundMessage(
        network=policy.network,
        gateway_id=gateway_id,
        sender_endpoint=sender_endpoint,
        actor_class=actor_class,
        content_class=content_class,
        conversation_scope=None,
        action="log_only",
        reason=reason,
        is_valid_ehn=False,
        requires_master_lookup=False,
        ham_plaintext_gate_required=policy.outbound_ham_plaintext_required,
        target=None,
        body=str(message.text or "").strip(),
    )