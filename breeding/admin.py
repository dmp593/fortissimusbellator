from django.contrib import admin
from modeltranslation.admin import TranslationAdmin
from attachments.admin import AttachmentStackedInline
from tags.admin import TagAdminStackedInline

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


@admin.register(models.Animal)
class AnimalAdmin(TranslationAdmin):
    """
    Admin configuration for Animal, including attachments and certifications.
    """
    form = forms.AnimalForm

    inlines = [
        AttachmentStackedInline,
        AnimalCertificationStackedInline,
        TagAdminStackedInline,
    ]

    list_display = (
        'name',
        'breed',
        'current_price_in_euros',
        'for_sale',
        'active',
    )

    list_filter = (
        'breed',
        'for_sale',
        'active',
    )

    ordering = ('order',)


@admin.register(models.Litter)
class LitterAdmin(TranslationAdmin):
    """
    Litter configuration for Animal, including attachments.
    """
    inlines = [
        AttachmentStackedInline,
        TagAdminStackedInline
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
