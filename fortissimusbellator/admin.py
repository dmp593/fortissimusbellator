from django.contrib.admin import site
from django.utils.translation import gettext_lazy as _

from modeltranslation.admin import TranslationAdmin

from fortissimusbellator.translator import translate


site.site_title = _('Fortissimus Bellator Site Admin')
site.site_header = _('Fortissimus Bellator Administration')


class FieldTranslatorAdmin(TranslationAdmin):
    translation_fields = ()

    def save_model(self, request, obj, form, change):
        for field in self.translation_fields:
            self._fill_missing_translations(
                obj,
                field,
                form.cleaned_data,
            )

        super().save_model(request, obj, form, change)

    def _fill_missing_translations(self, obj, field, cleaned_data):
        languages = ("pt", "en", "es", "fr", "de", "it")
        field_names = {
            language: f"{field}_{language}"
            for language in languages
        }
        if not all(name in cleaned_data for name in field_names.values()):
            return

        values = {
            language: cleaned_data.get(name)
            for language, name in field_names.items()
        }
        if not values["pt"] and values["en"]:
            values["pt"] = self._translate_to_portuguese(values["en"])
            self._set_translation(obj, field_names["pt"], values["pt"])

        if not values["en"] and values["pt"]:
            values["en"] = self._translate(
                values["pt"],
                source_lang="pt",
                target_lang="en",
            )
            self._set_translation(obj, field_names["en"], values["en"])

        if not values["en"]:
            return
        for language in ("es", "fr", "de", "it"):
            if values[language]:
                continue
            translated = self._translate(
                values["en"],
                source_lang="en",
                target_lang=language,
            )
            self._set_translation(
                obj,
                field_names[language],
                translated,
            )

    def _translate_to_portuguese(self, text):
        translated = self._translate(
            text,
            source_lang="en",
            target_lang="pt-pt",
            provider="deepl",
        )
        return translated or self._translate(
            text,
            source_lang="en",
            target_lang="pt",
        )

    @staticmethod
    def _translate(
        text,
        *,
        source_lang,
        target_lang,
        provider="google",
    ):
        return translate(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            provider=provider,
        )

    @staticmethod
    def _set_translation(obj, field_name, value):
        if value:
            setattr(obj, field_name, value)
