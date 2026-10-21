from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ehn_basestation.directory.delivery_store import DeliveryStore


DEFAULT_LANGUAGE = "en"
DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "registered": {"en": "registered"},
    "unknown_user": {"en": "unknown user"},
    "message_not_allowed": {"en": "message not allowed"},
    "group_not_available": {"en": "group not available"},
    "message_stored": {"en": "message stored"},
    "fallback_sent": {"en": "fallback sent"},
    "delivery_expired": {"en": "delivery expired"},
}


@dataclass(slots=True)
class ReplyTemplateResolver:
    delivery_store: DeliveryStore
    default_language: str = DEFAULT_LANGUAGE

    def ensure_defaults(self) -> None:
        for template_key, translations in DEFAULT_TEMPLATES.items():
            for language_code, template_text in translations.items():
                self.delivery_store.save_reply_template(
                    template_key=template_key,
                    language_code=language_code,
                    template_text=template_text,
                )

    def render(
        self,
        template_key: str,
        *,
        preferred_language: str | None = None,
        tokens: Mapping[str, object] | None = None,
    ) -> str:
        normalized_key = str(template_key).strip()
        language_code = str(preferred_language or "").strip().lower() or self.default_language
        template_text = self.delivery_store.get_reply_template(
            template_key=normalized_key,
            language_code=language_code,
            fallback_language=self.default_language,
        )
        if template_text is None:
            template_text = DEFAULT_TEMPLATES.get(normalized_key, {}).get(self.default_language, normalized_key)
        if not tokens:
            return template_text
        safe_tokens = {key: str(value) for key, value in tokens.items()}
        return template_text.format(**safe_tokens)