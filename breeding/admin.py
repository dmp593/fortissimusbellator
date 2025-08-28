from django.contrib import admin
from modeltranslation.admin import TranslationAdmin
from attachments.admin import AttachmentStackedInline
from tags.admin import TagAdminStackedInline

from fortissimusbellator import deepl

from . import forms
from .translation import models


class AnimalCertificationStackedInline(admin.StackedInline):
    """
    Inline for managing certifications related to an Animal.
    """
    model = models.AnimalCertification
    extra = 1  # Number of empty forms to display by default


@admin.register(models.AnimalKind)
class AnimalKindAdmin(TranslationAdmin):
    """
    Admin configuration for AnimalKind.
    """
    list_display = ('name', 'order')
    ordering = ('order',)


@admin.register(models.Breed)
class BreedAdmin(TranslationAdmin):
    """
    Admin configuration for Breed.
    """
    list_display = (
        'name',
        'kind',
        'parent',
        'order',
    )

    list_filter = (
        'kind',
        'parent',
    )

    ordering = ('order',)


@admin.register(models.Certification)
class CertificationAdmin(TranslationAdmin):
    """
    Admin configuration for Certification.
    """
    list_display = (
        'code',
        'name',
        'parent',
        'order',
    )

    list_filter = (
        'parent',
        'breeds',
    )

    ordering = ('order',)


class DeeplTranslationAdmin(TranslationAdmin):
    def save_model(self, request, obj, form, change):
        deepl_translation_fields = getattr(
            self, 'deepl_translation_fields', []
        )

        cleaned_data = form.cleaned_data

        for field in deepl_translation_fields:
            field_pt = f"{field}_pt"
            field_en = f"{field}_en"

            if field_pt not in cleaned_data or field_en not in cleaned_data:
                continue

            field_pt_value = cleaned_data.get(field_pt)
            field_en_value = cleaned_data.get(field_en)

            if not field_pt_value and field_en_value:
                translation_pt = deepl.trans(field_en_value, "en", "pt-pt")
                setattr(obj, field_pt, translation_pt)

            if not field_en_value and field_pt_value:
                translation_en = deepl.trans(field_pt_value, "pt-pt", "en")
                setattr(obj, field_en, translation_en)

        super().save_model(request, obj, form, change)


@admin.register(models.Animal)
class AnimalAdmin(DeeplTranslationAdmin):
    """
    Admin configuration for Animal, including attachments and certifications.
    """
    form = forms.AnimalForm

    deepl_translation_fields = [
        'description'
    ]

    inlines = [
        AttachmentStackedInline,
        AnimalCertificationStackedInline,
        TagAdminStackedInline,
    ]

    list_display = (
        'name',
        'breed',
        'current_price_in_euros',
        'has_training',
        'active',
        'order',
    )

    list_filter = (
        'breed',
        'for_sale',
        'active',
    )

    list_editable = (
        'has_training',
        'active',
        'order',
    )

    ordering = ('order',)


@admin.register(models.Litter)
class LitterAdmin(DeeplTranslationAdmin):
    """
    Litter configuration for Animal, including attachments.
    """
    inlines = [
        AttachmentStackedInline,
        TagAdminStackedInline
    ]

    deepl_translation_fields = [
        'description',
    ]

    list_display = (
        'name',
        'breed',
        'expected_birth_date',
        'expected_babies',
        'active',
    )

    list_filter = (
        'breed',
        'active',
    )

    ordering = ('order',)
