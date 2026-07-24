"""Synchronous, request-local adapters for optional translation providers."""

import asyncio
import logging

from django.conf import settings
from django.utils.translation import get_language


logger = logging.getLogger(__name__)


def translate(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None,
    provider: str = "deepl",
) -> str | None:
    """Translate text without retaining provider clients between calls."""
    target_lang = target_lang or get_language() or "en"
    try:
        if provider == "google":
            return _translate_with_google(text, source_lang, target_lang)
        if provider == "deepl":
            return _translate_with_deepl(text, source_lang, target_lang)
    except Exception:
        logger.exception(
            "translation_failed provider=%s source=%s target=%s",
            provider,
            source_lang or "auto",
            target_lang,
        )
        return None

    logger.error("translation_provider_unsupported provider=%s", provider)
    return None


def _translate_with_google(text, source_lang, target_lang):
    async def request_translation():
        from googletrans import Translator

        async with Translator() as client:
            result = await client.translate(
                text,
                src=source_lang or "auto",
                dest=target_lang,
            )
            return result.text

    return asyncio.run(request_translation())


def _translate_with_deepl(text, source_lang, target_lang):
    if not settings.DEEPL_AUTH_KEY:
        raise RuntimeError("DeepL is not configured.")

    from deepl import DeepLClient

    client = DeepLClient(settings.DEEPL_AUTH_KEY)
    try:
        result = client.translate_text(
            text,
            source_lang=source_lang.upper() if source_lang else None,
            target_lang=target_lang.upper(),
        )
        return result.text
    finally:
        client.close()
