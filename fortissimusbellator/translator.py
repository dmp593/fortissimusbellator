import logging

from asyncio import get_event_loop
from django.conf import settings
from django.utils.translation import get_language

from deepl import DeepLClient
from googletrans import Translator


google_translator = Translator()
deepl_client = DeepLClient(settings.DEEPL_AUTH_KEY)

__translation_providers__ = {
    "google": lambda text, src, dst: get_event_loop().run_until_complete(
        google_translator.translate(
            text,
            src=src,
            dest=dst or get_language()
        )
    ),
    "deepl": lambda text, src, dst: deepl_client.translate_text(
        text,
        source_lang=src,
        target_lang=dst or get_language()
    )
}


def translate(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None,
    provider: str = "deepl"
) -> str | None:
    if provider not in __translation_providers__:
        logging.error("Unsupported translation provider: %s", provider)
        return None

    try:
        text_result = __translation_providers__[provider](
            text, source_lang, target_lang
        )

        return text_result.text
    except Exception as ex:  # pylint: disable=bare-except
        logging.error("Translate error: %s", ex)
        return None
