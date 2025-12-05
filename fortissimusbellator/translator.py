import asyncio
import logging

from django.conf import settings
from django.utils.translation import get_language

from deepl import DeepLClient
from googletrans import Translator


__google_translator = Translator()
__deepl_client = DeepLClient(settings.DEEPL_AUTH_KEY)


def __get_event_loop():
    loop = None

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError as e:
        if str(e).startswith('There is no current event loop in thread'):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

    return loop


def __google_translate(
    text: str,
    src: str | None = None,
    dst: str | None = None
) -> str | None:
    loop = __get_event_loop()

    if not loop:
        return None

    return loop.run_until_complete(
        __google_translator.translate(
            text,
            src=src,
            dest=dst or get_language()
        )
    )


def __deepl_translate(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None
):
    __deepl_client.translate_text(
        text,
        source_lang=source_lang,
        target_lang=target_lang or get_language()
    )


__translation_providers__ = {
    "google": __google_translate,
    "deepl": __deepl_translate
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
