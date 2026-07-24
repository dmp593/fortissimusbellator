from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from attachments.admin import AttachmentStackedInline
from attachments.models import Attachment

from tags.admin import TagAdminStackedInline
from tags.models import Tag

from chat.admin_aliases import ChatAliasSuggestionsAdminMixin
from fortissimusbellator.admin import TranslationAdmin, FieldTranslatorAdmin
from reservations.admin_mixins import ReservationHistoryDeleteMixin
from breeding.services.litter_alerts import process_birth_notification

from . import forms
from .translation import models
from .social_media import publish_animal


class AnimalSaleStateFilter(admin.SimpleListFilter):
    title = _('sale state')
    parameter_name = 'sale_state'

    def lookups(self, request, model_admin):
        return (
            ('available', _('Not sold')),
            ('sold', _('Sold')),
        )

    def queryset(self, request, queryset):
        if self.value() == 'sold':
            return queryset.filter(has_completed_sale=True)
        if self.value() == 'available':
            return queryset.filter(has_completed_sale=False)
        return queryset


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
            litter_babies = litter.babies or 0

            if litter_babies <= 0:
                messages.warning(
                    request,
                    _(
                        'Litter "%(litter_name)s" has no actual babies born '
                        'to create.'
                    ) % {
                        'litter_name': litter.name
                    }
                )
                continue
            if not litter.birth_date:
                messages.warning(
                    request,
                    _(
                        'Litter "%(litter_name)s" needs an actual birth date '
                        'before its babies can be created.'
                    ) % {'litter_name': litter.name},
                )
                continue

            existing_animals_count = litter.animals.count()
            babies_to_create = litter_babies - existing_animals_count

            if babies_to_create <= 0:
                messages.warning(
                    request,
                    _(
                        'Litter "%(litter_name)s" has no remaining babies to '
                        'create: %(existing_count)d already created.'
                    ) % {
                        'litter_name': litter.name,
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
            translated_descriptions = {
                field_name: getattr(litter, field_name)
                for field_name in (
                    'description_en',
                    'description_pt',
                    'description_es',
                    'description_fr',
                    'description_de',
                    'description_it',
                )
                if hasattr(litter, field_name)
            }

            for index in range(babies_to_create):
                animal = models.Animal.objects.create(
                    breed=litter.breed,
                    name=_(
                        "Offspring {offspring_number} from litter {litter_name}"
                    ).format(
                        offspring_number=existing_animals_count + index + 1,
                        litter_name=litter.name,
                    ),
                    **translated_descriptions,
                    birth_date=litter.birth_date or litter.expected_birth_date,
                    gender='?',
                    hair_type='?',
                    father=litter.father,
                    mother=litter.mother,
                    litter=litter,
                    for_sale=True,
                    pre_reservation_enabled=(
                        litter.offspring_pre_reservation_enabled
                    ),
                    pre_reservation_fee=litter.offspring_pre_reservation_fee,
                    reservation_deposit_percentage=(
                        litter.offspring_reservation_deposit_percentage
                    ),
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

            messages.success(
                request,
                _(
                    'Created %(babies_count)d remaining babies for litter '
                    '"%(litter_name)s".'
                ) % {
                    'babies_count': babies_to_create,
                    'litter_name': litter.name,
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
    search_fields = ('name',)
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

    search_fields = ('name', 'kind__name', 'parent__name')
    autocomplete_fields = ('parent',)
    list_per_page = 50
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

    search_fields = ('code', 'name', 'parent__name')
    autocomplete_fields = ('parent',)
    list_per_page = 50
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
    change_form_template = 'admin/breeding/animal/change_form.html'

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
        'sale_state',
        'pre_reservation_enabled',
        'pre_reservation_fee',
        'reservation_deposit_percentage',
        'reservation_offer_hours',
        'has_training',
        'active',
        'order',
    )

    list_filter = (
        'breed__kind',
        'breed',
        'gender',
        'litter',
        'for_sale',
        AnimalSaleStateFilter,
        'active',
        'pre_reservation_enabled',
    )

    search_fields = (
        'name',
        'breed__name',
        'litter__name',
        'sale_cases__sale__public_id',
        'sale_cases__user__username',
        'sale_cases__user__email',
    )

    autocomplete_fields = (
        'father',
        'mother',
        'litter',
    )

    list_editable = (
        'has_training',
        'order',
    )

    readonly_fields = ('sale_summary',)
    ordering = ('name',)
    list_per_page = 50

    def get_queryset(self, request):
        from reservations.availability import annotate_dog_availability

        return annotate_dog_availability(
            super().get_queryset(request),
        )

    @admin.display(description=_('sale state'), ordering='has_completed_sale')
    def sale_state(self, obj):
        return _('Sold') if obj.is_sold else _('Not sold')

    @admin.display(description=_('commercial sale'))
    def sale_summary(self, obj):
        if not obj or not obj.pk:
            return '-'
        from reservations.models import AnimalSaleCase

        completed_sale_case = (
            obj.sale_cases.filter(
                sale__isnull=False,
                sale__voided_at__isnull=True,
            )
            .select_related('sale', 'user')
            .first()
        )
        if completed_sale_case is None and (
            obj.has_blocking_sale_case
            or obj.has_blocking_pre_reservation
            or obj.has_confirmed_reservation
        ):
            active_case = obj.sale_cases.filter(
                status__in=(
                    AnimalSaleCase.Status.PRE_RESERVATION,
                    AnimalSaleCase.Status.RESERVATION,
                ),
            ).first()
            if active_case is None:
                return _('This dog is already held by another process.')
            url = reverse(
                'admin:reservations_animalsalecase_change',
                args=[active_case.pk],
            )
            return format_html(
                '<a href="{}">{}</a>',
                url,
                _('Open sale process'),
            )
        if completed_sale_case is None:
            url = (
                f"{reverse('admin:reservations_animalsalecase_add')}"
                f'?animal={obj.pk}&start_stage=sale'
            )
            return format_html(
                '<a href="{}">{}</a>',
                url,
                _('Register a direct final sale'),
            )
        sale = completed_sale_case.sale
        price = (
            f'{sale.final_price} {completed_sale_case.currency}'
            if sale.final_price is not None
            else str(_('Final price not recorded (legacy sale)'))
        )
        url = reverse(
            'admin:reservations_animalsale_change',
            args=[sale.pk],
        )
        return format_html(
            '<a href="{}">{}</a> · {} · {}',
            url,
            _('Open sale record'),
            sale.sold_at,
            price,
        )


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

        'offspring_pre_reservation_enabled',
        'offspring_pre_reservation_fee',
        'offspring_reservation_deposit_percentage',

        'status',
        'active',
    )

    list_filter = (
        'breed__kind',
        'breed',
        'status',
        'active',
        'offspring_pre_reservation_enabled',
        'expected_birth_date',
        'birth_date',
    )

    search_fields = (
        'name',
        'breed__name',
        'father__name',
        'mother__name',
    )

    autocomplete_fields = ('father', 'mother')
    ordering = ('-birth_date', '-expected_birth_date', 'name')
    list_per_page = 50


@admin.register(models.LitterAlertPreference)
class LitterAlertPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'scope', 'language_code', 'updated_at')
    list_filter = ('scope',)
    search_fields = ('user__username', 'user__email')
    filter_horizontal = ('breeds',)


@admin.register(models.LitterAlertOverride)
class LitterAlertOverrideAdmin(admin.ModelAdmin):
    list_display = ('user', 'litter', 'enabled', 'updated_at')
    list_filter = ('enabled', 'litter__breed')
    search_fields = ('user__username', 'user__email', 'litter__name')


@admin.register(models.LitterBirthAnnouncement)
class LitterBirthAnnouncementAdmin(admin.ModelAdmin):
    list_display = (
        'litter_name',
        'breed_name',
        'babies',
        'birth_date',
        'announced_at',
    )
    readonly_fields = (
        'litter',
        'litter_name',
        'breed_name',
        'babies',
        'birth_date',
        'announced_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.LitterBirthNotification)
class LitterBirthNotificationAdmin(admin.ModelAdmin):
    list_display = (
        'announcement',
        'recipient',
        'status',
        'attempt_count',
        'sent_at',
    )
    list_filter = ('status',)
    search_fields = ('recipient', 'announcement__litter_name')
    actions = ('retry_selected_notifications',)
    readonly_fields = (
        'announcement',
        'user',
        'recipient',
        'language_code',
        'status',
        'attempt_count',
        'processing_started_at',
        'next_retry_at',
        'last_error',
        'sent_at',
        'created_at',
        'updated_at',
    )

    @admin.action(description=_('Retry selected birth notifications'))
    def retry_selected_notifications(self, request, queryset):
        for notification in queryset:
            result = process_birth_notification(notification.pk)
            self.message_user(
                request,
                f'{notification.recipient}: {result.get_status_display()}',
            )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
