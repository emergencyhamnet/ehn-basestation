from __future__ import annotations

import unittest

from ehn_basestation.core.message_policy import (
    InboundTextMessage,
    classify_inbound_text_message,
    get_transport_policy,
)


def _sms(text: str, **overrides) -> InboundTextMessage:
    fields = {
        "gateway_id": "EHN-NNJ",
        "network": "sms",
        "sender_endpoint": "+15550100",
        "text": text,
    }
    fields.update(overrides)
    return InboundTextMessage(**fields)


def _channel(text: str, **overrides) -> InboundTextMessage:
    fields = {
        "gateway_id": "ehn-nnj",
        "network": "meshcore",
        "sender_endpoint": "PUBKEY-ALICE",
        "text": text,
        "is_ehn_group_channel": True,
    }
    fields.update(overrides)
    return InboundTextMessage(**fields)


class DirectTargetTests(unittest.TestCase):
    def test_at_user_routes_single_and_lowercases_user_id(self) -> None:
        result = classify_inbound_text_message(_sms("@Jane-1234 Need status update"))

        self.assertEqual(result.action, "route")
        self.assertTrue(result.is_valid_ehn)
        self.assertEqual(result.conversation_scope, "single")
        self.assertEqual(result.target.target_type, "user")
        self.assertEqual(result.target.target_id, "jane-1234")
        self.assertEqual(result.body, "Need status update")

    def test_body_whitespace_is_trimmed_but_content_preserved(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh   Meet at  Shelter One  "))

        self.assertEqual(result.body, "Meet at  Shelter One")

    def test_tab_separator_is_accepted(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh\tStatus update"))

        self.assertEqual(result.target.target_id, "n2dh")
        self.assertEqual(result.body, "Status update")


class GroupTargetTests(unittest.TestCase):
    def test_local_ampersand_routes_to_station_group(self) -> None:
        result = classify_inbound_text_message(_sms("& Shelter supplies arrived"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.conversation_scope, "group")
        self.assertEqual(result.target.target_type, "group")
        self.assertEqual(result.target.target_id, "EHN-NNJ")
        self.assertEqual(result.body, "Shelter supplies arrived")

    def test_station_alias_is_case_insensitive(self) -> None:
        result = classify_inbound_text_message(_sms("&nnj Shelter supplies arrived"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.target.target_id, "EHN-NNJ")

    def test_retired_long_group_id_is_rejected(self) -> None:
        result = classify_inbound_text_message(_sms("&EHN-NNJ Shelter supplies arrived"))

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "retired_group_id")

    def test_unknown_named_group_is_not_local(self) -> None:
        result = classify_inbound_text_message(_sms("&OTHER Shelter supplies arrived"))

        self.assertEqual(result.target.target_id, "OTHER")

    def test_bare_gateway_id_is_not_a_group_target(self) -> None:
        result = classify_inbound_text_message(_sms("EHN-NNJ Shelter supplies arrived"))

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "unaddressed_text")


class SinkTargetTests(unittest.TestCase):
    def test_hash_sink_routes_device_telemetry_without_conversation_scope(self) -> None:
        result = classify_inbound_text_message(
            _sms(
                "#School-Weather 21.4C 68% 1012hPa",
                sender_endpoint="device-weather-1",
                actor_class="device",
                content_class="telemetry",
            )
        )

        self.assertEqual(result.action, "route")
        self.assertEqual(result.actor_class, "device")
        self.assertEqual(result.content_class, "telemetry")
        self.assertIsNone(result.conversation_scope)
        self.assertEqual(result.target.target_type, "sink")
        self.assertEqual(result.target.target_id, "school-weather")
        self.assertEqual(result.body, "21.4C 68% 1012hPa")

    def test_person_may_submit_to_sink(self) -> None:
        result = classify_inbound_text_message(_sms("#school-weather rain gauge reset"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.actor_class, "person")
        self.assertEqual(result.target.target_type, "sink")


class RegistrationTests(unittest.TestCase):
    def test_register_alone_is_registration_on_sms(self) -> None:
        result = classify_inbound_text_message(_sms("register"))

        self.assertEqual(result.action, "register")
        self.assertTrue(result.is_valid_ehn)
        self.assertTrue(result.requires_master_lookup)
        self.assertEqual(result.conversation_scope, "single")
        self.assertEqual(result.target.target_type, "group")
        self.assertEqual(result.target.target_id, "EHN-NNJ")
        self.assertEqual(result.body, "register")

    def test_register_is_case_insensitive_and_trimmed(self) -> None:
        result = classify_inbound_text_message(_sms("  REGISTER  "))

        self.assertEqual(result.action, "register")

    def test_register_on_private_channel_is_group_scoped(self) -> None:
        result = classify_inbound_text_message(_channel("register"))

        self.assertEqual(result.action, "register")
        self.assertEqual(result.conversation_scope, "group")
        self.assertEqual(result.target.target_id, "EHN-NNJ")

    def test_addressed_register_body_is_ordinary_routed_text(self) -> None:
        result = classify_inbound_text_message(_sms("& register"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.body, "register")

    def test_legacy_ehn_colon_probe_is_rejected(self) -> None:
        result = classify_inbound_text_message(_sms("EHN:"))

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "legacy_prefix")


class LegacyPrefixTests(unittest.TestCase):
    def test_legacy_direct_form_is_rejected(self) -> None:
        result = classify_inbound_text_message(_sms("EHN @jane-1234 Need status update"))

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "legacy_prefix")
        self.assertFalse(result.is_valid_ehn)
        self.assertFalse(result.requires_master_lookup)

    def test_legacy_group_form_is_rejected(self) -> None:
        result = classify_inbound_text_message(_sms("ehn EHN-NNJ Shelter supplies arrived"))

        self.assertEqual(result.reason, "legacy_prefix")

    def test_legacy_prefix_is_rejected_even_on_private_channel(self) -> None:
        result = classify_inbound_text_message(_channel("EHN @n2dh hello"))

        self.assertEqual(result.reason, "legacy_prefix")


class RejectionTests(unittest.TestCase):
    def test_empty_message(self) -> None:
        result = classify_inbound_text_message(_sms("   "))

        self.assertEqual(result.reason, "empty_message")

    def test_unaddressed_text_on_external_transport(self) -> None:
        result = classify_inbound_text_message(_sms("Need status update"))

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "unaddressed_text")

    def test_unknown_target_prefix(self) -> None:
        result = classify_inbound_text_message(_sms("!n2dh hello"))

        self.assertEqual(result.reason, "unknown_target_prefix")

    def test_empty_target(self) -> None:
        result = classify_inbound_text_message(_sms("@ Need status update"))

        self.assertEqual(result.reason, "empty_target")

    def test_missing_body(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh"))

        self.assertEqual(result.reason, "missing_body")

    def test_invalid_target_characters(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh/ops hello"))

        self.assertEqual(result.reason, "invalid_target")

    def test_identifier_allows_dash_underscore_and_dot(self) -> None:
        result = classify_inbound_text_message(_sms("#site_a.rain-1 3.2mm"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.target.target_id, "site_a.rain-1")

    def test_body_is_never_scanned_for_addressing(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh tell &EHN-NNJ and #sink nothing"))

        self.assertEqual(result.target.target_type, "user")
        self.assertEqual(result.body, "tell &EHN-NNJ and #sink nothing")

    def test_mesh_direct_message_without_address_is_rejected(self) -> None:
        result = classify_inbound_text_message(
            InboundTextMessage(
                gateway_id="EHN-NNJ",
                network="meshcore",
                sender_endpoint="PUBKEY-ALICE",
                text="hello base",
            )
        )

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "unaddressed_text")
        self.assertFalse(result.requires_master_lookup)


class PrivateChannelTests(unittest.TestCase):
    def test_bare_text_on_channel_is_implicit_group_traffic(self) -> None:
        result = classify_inbound_text_message(_channel("Weather net active at shelter one"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.conversation_scope, "group")
        self.assertEqual(result.target.target_type, "group")
        self.assertEqual(result.target.target_id, "EHN-NNJ")
        self.assertEqual(result.body, "Weather net active at shelter one")
        self.assertTrue(result.ham_plaintext_gate_required)

    def test_symbol_leading_text_on_channel_stays_group_traffic(self) -> None:
        result = classify_inbound_text_message(_channel("!!! need water at shelter two"))

        self.assertEqual(result.action, "route")
        self.assertEqual(result.target.target_type, "group")

    def test_explicit_user_target_on_channel_is_honored(self) -> None:
        result = classify_inbound_text_message(_channel("@n2dh private follow-up"))

        self.assertEqual(result.conversation_scope, "single")
        self.assertEqual(result.target.target_type, "user")
        self.assertEqual(result.target.target_id, "n2dh")

    def test_explicit_sink_target_on_channel_is_honored(self) -> None:
        result = classify_inbound_text_message(
            _channel("#school-weather 20C", actor_class="device", content_class="telemetry")
        )

        self.assertEqual(result.target.target_type, "sink")

    def test_malformed_explicit_target_on_channel_fails_closed(self) -> None:
        result = classify_inbound_text_message(_channel("@ hello everyone"))

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "empty_target")

    def test_meshtastic_shares_mesh_policy(self) -> None:
        result = classify_inbound_text_message(
            _channel("Shelter status green", gateway_id="EHN-CAL", network="meshtastic", sender_endpoint="NODE-1")
        )

        self.assertEqual(result.action, "route")
        self.assertEqual(result.conversation_scope, "group")
        self.assertTrue(result.ham_plaintext_gate_required)


class TransportPolicyTests(unittest.TestCase):
    def test_aprs_requires_plaintext_source(self) -> None:
        result = classify_inbound_text_message(
            InboundTextMessage(
                gateway_id="EHN-TEX",
                network="aprs",
                sender_endpoint="N0CALL-1",
                text="&EHN-TEX WX station update",
                is_plaintext=False,
            )
        )

        self.assertEqual(result.action, "log_only")
        self.assertEqual(result.reason, "non_plaintext_source")

    def test_ham_gate_flag_is_preserved_for_mesh_direct(self) -> None:
        result = classify_inbound_text_message(
            InboundTextMessage(
                gateway_id="EHN-NNJ",
                network="meshcore",
                sender_endpoint="PUBKEY-ALICE",
                text="@n2dh hello",
            )
        )

        self.assertTrue(result.ham_plaintext_gate_required)

    def test_sms_does_not_require_ham_gate(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh hello"))

        self.assertFalse(result.ham_plaintext_gate_required)

    def test_unknown_network_defaults_to_explicit_target_rules(self) -> None:
        profile = get_transport_policy("winlink")

        self.assertEqual(profile.network, "winlink")
        self.assertTrue(profile.requires_explicit_target)
        self.assertFalse(profile.implicit_group_channel_supported)

    def test_unsupported_actor_class_is_rejected(self) -> None:
        result = classify_inbound_text_message(_sms("@n2dh hello", actor_class="robot"))

        self.assertEqual(result.reason, "unsupported_actor_class")


if __name__ == "__main__":
    unittest.main()
