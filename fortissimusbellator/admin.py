from django.contrib.admin import site
from django.utils.translation import gettext_lazy as _

from modeltranslation.admin import TranslationAdmin

from fortissimusbellator import translator


site.site_title = _('Fortissimus Bellator Site Admin')
site.site_header = _('Fortissimus Bellator Administration')


class FieldTranslatorAdmin(TranslationAdmin):
    def save_model(self, request, obj, form, change):
        translation_fields = getattr(
            self, 'translation_fields', []
        )

        cleaned_data = form.cleaned_data

        for field in translation_fields:
            field_pt = f"{field}_pt"
            field_en = f"{field}_en"
            field_es = f"{field}_es"
            field_fr = f"{field}_fr"
            field_de = f"{field}_de"
            field_it = f"{field}_it"

            if (
                field_pt not in cleaned_data or
                field_en not in cleaned_data or
                field_es not in cleaned_data or
                field_fr not in cleaned_data or
                field_de not in cleaned_data or
                field_it not in cleaned_data
            ):
                continue

            field_pt_value = cleaned_data.get(field_pt)
            field_en_value = cleaned_data.get(field_en)
            field_es_value = cleaned_data.get(field_es)
            field_fr_value = cleaned_data.get(field_fr)
            field_de_value = cleaned_data.get(field_de)
            field_it_value = cleaned_data.get(field_it)

            # Translate missing fields

            # either from EN to PT, or from PT to EN
            if not field_pt_value and field_en_value:
                field_pt_value = translator.translate(
                    text=field_en_value,
                    source_lang="en",
                    target_lang="pt-pt",
                    provider="deepl"  # deepl has better PT-PT support
                )

                if not field_pt_value:
                    # fallback to google if deepl fails
                    # probably due to tokens limits (free tier)
                    field_pt_value = translator.translate(
                        text=field_en_value,
                        source_lang="en",
                        target_lang="pt",
                        provider="google"
                    )
                setattr(obj, field_pt, field_pt_value)

            if not field_en_value and field_pt_value:
                field_en_value = translator.translate(
                    text=field_pt_value,
                    source_lang="pt",
                    target_lang="en",
                    provider="google"
                )
                setattr(obj, field_en, field_en_value)

            # the other languages are always from EN
            if not field_es_value and field_en_value:
                field_es_value = translator.translate(
                    text=field_en_value,
                    source_lang="en",
                    target_lang="es",
                    provider="google"
                )
                setattr(obj, field_es, field_es_value)

            if not field_fr_value and field_en_value:
                field_fr_value = translator.translate(
                    text=field_en_value,
                    source_lang="en",
                    target_lang="fr",
                    provider="google"
                )
                setattr(obj, field_fr, field_fr_value)

            if not field_de_value and field_en_value:
                field_de_value = translator.translate(
                    text=field_en_value,
                    source_lang="en",
                    target_lang="de",
                    provider="google"
                )
                setattr(obj, field_de, field_de_value)

            if not field_it_value and field_en_value:
                field_it_value = translator.translate(
                    text=field_en_value,
                    source_lang="en",
                    target_lang="it",
                    provider="google"
                )
                setattr(obj, field_it, field_it_value)

        super().save_model(request, obj, form, change)
