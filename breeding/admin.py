from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from attachments.admin import AttachmentStackedInline
from attachments.models import Attachment

from tags.admin import TagAdminStackedInline
from tags.models import Tag

from chat.admin_aliases import ChatAliasSuggestionsAdminMixin
from fortissimusbellator.admin import TranslationAdmin, FieldTranslatorAdmin
from reservations.availability import litter_reserved_count
from reservations.admin_mixins import ReservationHistoryDeleteMixin

from . import forms
from .translation import models
from .social_media import publish_animal


@admin.action(description=_("Publish selected animals to Social Media"))
def publish_animals_social_media(modeladmin, request, queryset):
    for animal in queryset:
        results = publish_animal(animal)

        msg_parts = []
        for platform, status in results.items():
            msg_parts.append(f"{platform}: {status}")

        full_msg = f"Animal {animal.name}: " + ", ".join(msg_parts)

        if "Error" in full_msg:
            messages.warning(request, full_msg)
        else:
            messages.success(request, full_msg)


@admin.action(description=_("Create animals from selected litters"))
def create_animals_from_litter(modeladmin, request, queryset):
    for litter_id in queryset.values_list('pk', flat=True):
        with transaction.atomic():
            litter = models.Litter.objects.select_for_update().get(pk=litter_id)
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

            reserved_count = litter_reserved_count(litter)
            existing_animals_count = litter.animals.count()
            babies_to_create = (
                litter_babies - reserved_count - existing_animals_count
            )

            if babies_to_create <= 0:
                if litter.pre_reservation_capacity != reserved_count:
                    litter.pre_reservation_capacity = reserved_count
                    litter.save(update_fields=['pre_reservation_capacity'])
                messages.warning(
                    request,
                    _(
                        'Litter "%(litter_name)s" has no remaining babies to '
                        'create: %(reserved_count)d reserved and '
                        '%(existing_count)d already created.'
                    ) % {
                        'litter_name': litter.name,
                        'reserved_count': reserved_count,
                        'existing_count': existing_animals_count,
                    }
                )
                continue

            animal_content_type = ContentType.objects.get_for_model(models.Animal)
            litter_content_type = ContentType.objects.get_for_model(litter)
            litter_attachments = list(
                Attachment.objects.filter(
                    content_type=litter_content_type,
                    object_id=litter.id,
                )
            )
            litter_tags = list(
                Tag.objects.filter(
                    content_type=litter_content_type,
                    object_id=litter.id,
                )
            )

            for index in range(babies_to_create):
                animal = models.Animal.objects.create(
                    breed=litter.breed,
                    name=_(
                        "Offspring {offspring_number} from litter {litter_name}"
                    ).format(
                        offspring_number=existing_animals_count + index + 1,
                        litter_name=litter.name,
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
                    pre_reservation_enabled=litter.pre_reservation_enabled,
                    pre_reservation_fee=litter.pre_reservation_fee,
                )

                for attachment in litter_attachments:
                    Attachment.objects.create(
                        file=attachment.file,
                        thumbnail=attachment.thumbnail,
                        content_type=animal_content_type,
                        object_id=animal.id,
                        description=attachment.description,
                        filename=attachment.filename,
                        mime_type=attachment.mime_type,
                        order=attachment.order,
                    )

                for tag in litter_tags:
                    Tag.objects.create(
                        tag=tag.tag,
                        color_light=tag.color_light,
                        color_dark=tag.color_dark,
                        content_type=animal_content_type,
                        object_id=animal.id,
                    )

            # Remaining inventory is now represented by individual animals.
            if litter.pre_reservation_capacity != reserved_count:
                litter.pre_reservation_capacity = reserved_count
                litter.save(update_fields=['pre_reservation_capacity'])

            messages.success(
                request,
                _(
                    'Created %(babies_count)d remaining babies for litter '
                    '"%(litter_name)s"; %(reserved_count)d places were already '
                    'reserved.'
                ) % {
                    'babies_count': babies_to_create,
                    'litter_name': litter.name,
                    'reserved_count': reserved_count,
                }
            )


class AnimalCertificationStackedInline(admin.StackedInline):
    """
    Inline for managing certifications related to an Animal.
    """
    model = models.AnimalCertification
    extra = 1  # Number of empty forms to display by default


@admin.register(models.AnimalKind)
class AnimalKindAdmin(ChatAliasSuggestionsAdminMixin, TranslationAdmin):
    """
    Admin configuration for AnimalKind.
    """
    list_display = ('name', 'order')
    ordering = ('order',)


@admin.register(models.Breed)
class BreedAdmin(ChatAliasSuggestionsAdminMixin, TranslationAdmin):
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
class CertificationAdmin(ChatAliasSuggestionsAdminMixin, TranslationAdmin):
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
class AnimalAdmin(
    ChatAliasSuggestionsAdminMixin,
    ReservationHistoryDeleteMixin,
    FieldTranslatorAdmin,
):
    """
    Admin configuration for Animal, including attachments and certifications.
    """
    form = forms.AnimalForm

    actions = [
        publish_animals_social_media
    ]

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
        'pre_reservation_enabled',
        'pre_reservation_fee',
        'has_training',
        'active',
        'order',
    )

    list_filter = (
        'breed',
        'for_sale',
        'active',
        'pre_reservation_enabled',
    )

    list_editable = (
        'has_training',
        'order',
    )

    ordering = ('order',)


@admin.register(models.Litter)
class LitterAdmin(
    ChatAliasSuggestionsAdminMixin,
    ReservationHistoryDeleteMixin,
    FieldTranslatorAdmin,
):
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

        'pre_reservation_enabled',
        'pre_reservation_capacity',
        'pre_reservation_fee',

        'status',
        'active',
    )

    list_filter = (
        'breed',
        'status',
        'active',
        'pre_reservation_enabled',
    )

    ordering = (
        'order',
    )
