import logging

from django.conf import settings
from django.utils.translation import get_language

from deepl import DeepLClient


def trans(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None
) -> str | None:
    try:
        deepl_client = DeepLClient(settings.DEEPL_AUTH_KEY)

        text_result = deepl_client.translate_text(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang or get_language()
        )

        return text_result.text
    except Exception as e:  # pylint: disable=bare-except
        logging.error("DeepL translation error: %s", e)
        return None
