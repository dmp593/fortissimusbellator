import logging

from django.utils.translation import get_language

from googletrans import Translator


def trans(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None
) -> str | None:
    try:
        translator = Translator()

        text_result = translator.translate(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang or get_language()
        )

        return text_result.text
    except Exception as e:  # pylint: disable=bare-except
        logging.error("GoogleTrans translation error: %s", e)
        return None
