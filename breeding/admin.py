from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from modeltranslation.admin import TranslationAdmin

from attachments.admin import AttachmentStackedInline
from attachments.models import Attachment

from tags.admin import TagAdminStackedInline
from tags.models import Tag

from fortissimusbellator import translator

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
