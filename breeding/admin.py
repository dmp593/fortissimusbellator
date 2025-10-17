from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from modeltranslation.admin import TranslationAdmin

from attachments.admin import AttachmentStackedInline
from attachments.models import Attachment

from tags.admin import TagAdminStackedInline
from tags.models import Tag

from fortissimusbellator import deepl

from . import forms
from .translation import models


@admin.action(description=_("Create animals from selected litters"))
def create_animals_from_litter(modeladmin, request, queryset):
    total_animals_created = 0

    for litter in queryset:
        animals_for_this_litter = 0
        expected_count = litter.babies or litter.expected_babies or 0

        for i in range(expected_count):
            # Create each animal individually to get the ID
            animal = models.Animal.objects.create(
                breed=litter.breed,

                name=_(
                    "Offspring {offspring_number} from litter {litter_name}"
                ).format(
                    litter_name=litter.name,
                    offspring_number=i + 1
                ),

                description_en=litter.description_en,
                description_pt=litter.description_pt,

                birth_date=litter.birth_date or litter.expected_birth_date,
                gender='?',
                hair_type='?',

                father=litter.father,
                mother=litter.mother,
                litter=litter,

                for_sale=True,
            )

            # Get content types
            animal_content_type = ContentType.objects.get_for_model(models.Animal)

            # Copy attachments from litter to animal
            litter_attachments = Attachment.objects.filter(
                content_type=ContentType.objects.get_for_model(litter),
                object_id=litter.id
            )

            for attachment in litter_attachments:
                Attachment.objects.create(
                    file=attachment.file,  # This will reference the same file
                    thumbnail=attachment.thumbnail,
                    content_type=animal_content_type,
                    object_id=animal.id,
                    description=attachment.description,
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    order=attachment.order,
                )

            # Copy tags from litter to animal
            litter_tags = Tag.objects.filter(
                content_type=ContentType.objects.get_for_model(litter),
                object_id=litter.id
            )

            for tag in litter_tags:
                Tag.objects.create(
                    tag=tag.tag,
                    color_light=tag.color_light,
                    color_dark=tag.color_dark,
                    content_type=animal_content_type,
                    object_id=animal.id,
                )

            animals_for_this_litter += 1
            total_animals_created += 1

    # Provide user feedback
    if total_animals_created > 0:
        messages.success(
            request,
            _(
                "Successfully created {total_animals_created} animals"
                " from {queryset_count} litter(s).",
            ).format(
                total_animals_created=total_animals_created,
                queryset_count=queryset.count()
            )
        )
    else:
        messages.warning(
            request,
            _(
                "No animals were created. Check that litters have 'babies'"
                " or 'expected_babies' values."
            )
        )


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
                translation_en = deepl.trans(field_pt_value, "pt", "en-us")
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
    actions = [create_animals_from_litter]

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

    list_editable = (
        'expected_birth_date',
        'expected_babies',
        'active',
    )

    ordering = (
        'order',
    )
