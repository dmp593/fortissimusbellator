from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from attachments.admin import AttachmentStackedInline
from attachments.models import Attachment

from tags.admin import TagAdminStackedInline
from tags.models import Tag

from fortissimusbellator.admin import TranslationAdmin, FieldTranslatorAdmin

from . import forms
from .translation import models


@admin.action(description=_("Create animals from selected litters"))
def create_animals_from_litter(modeladmin, request, queryset):
    for litter in queryset:
        litter_babies = litter.babies or litter.expected_babies or 0

        if litter_babies <= 0:
            messages.warning(
                request,
                _(
                    'Litter "%(litter_name)s" has no babies to create.'
                ) % {
                    'litter_name': litter.name
                }
            )

            continue

        for i in range(litter_babies):
            # Create each animal individually to get the ID
            animal = models.Animal.objects.create(
                breed=litter.breed,

                name=_(
                    "Offspring {offspring_number} from litter {litter_name}"
                ).format(
                    offspring_number=i + 1,
                    litter_name=litter.name
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

        # Provide user feedback
        messages.success(
            request,
            _(
                'Created %(babies_count)d babies for litter "%(litter_name)s".'
            ) % {
                'babies_count': litter_babies,
                'litter_name': litter.name
            }
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


@admin.register(models.Animal)
class AnimalAdmin(FieldTranslatorAdmin):
    """
    Admin configuration for Animal, including attachments and certifications.
    """
    form = forms.AnimalForm

    translation_fields = [
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
class LitterAdmin(FieldTranslatorAdmin):
    """
    Litter configuration for Animal, including attachments.
    """
    form = forms.LitterAdminForm

    actions = [
        create_animals_from_litter
    ]

    inlines = [
        AttachmentStackedInline,
        TagAdminStackedInline
    ]

    translation_fields = [
        'description',
    ]

    list_display = (
        'name',
        'breed',

        'expected_birth_date',
        'birth_date',

        'expected_ready_date',
        'ready_date',

        'expected_babies',
        'babies',

        'status',
        'active',
    )

    list_filter = (
        'breed',
        'status',
        'active',
    )

    list_editable = (
        'active',
    )

    ordering = (
        'order',
    )
