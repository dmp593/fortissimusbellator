import functools

import argostranslate.package
import argostranslate.translate

from django.utils.translation import get_language

# One-time setup: install a model (can be done at startup or manually beforehand)
# Example: English → Portuguese
# argostranslate.package.update_package_index()
# available_packages = argostranslate.package.get_available_packages()
# package = next(p for p in available_packages if p.from_code == "en" and p.to_code == "pt")
# argostranslate.package.install_from_path(package.download())


@functools.cache
def trans(
    text: str,
    source_lang: str | None = None,
    target_lang: str | None = None
) -> str:
    target_lang = target_lang or get_language()

    # Find a translator
    translators = argostranslate.translate.get_installed_languages()
    from_lang = None
    to_lang = None

    if source_lang:
        from_lang = next(
            (lang for lang in translators if lang.code == source_lang), None
        )
    else:
        # Argos doesn't auto-detect well → fallback to first available
        from_lang = translators[0]

    to_lang = next(
        (lang for lang in translators if lang.code == target_lang), None
    )

    if not from_lang or not to_lang:
        raise ValueError(
            f"No Argos model installed for {source_lang} → {target_lang}"
        )

    translation = from_lang.get_translation(to_lang)
    return translation.translate(text)
