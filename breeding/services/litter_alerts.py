import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext
from django.utils.translation import override

from breeding.models import (
    Litter,
    LitterAlertOverride,
    LitterAlertPreference,
    LitterBirthAnnouncement,
    LitterBirthNotification,
)
from fortissimusbellator.emails import (
    BrandedEmailContent,
    EmailAction,
    EmailDetail,
    absolute_reverse,
    absolute_url,
    format_email_date,
    send_branded_email,
)


logger = logging.getLogger(__name__)
User = get_user_model()
PROCESSING_LEASE = timedelta(minutes=10)


def get_or_create_alert_preference(user, *, language_code='en'):
    preference, created = LitterAlertPreference.objects.get_or_create(
        user=user,
        defaults={'language_code': language_code},
    )
    if not created and language_code and preference.language_code != language_code:
        preference.language_code = language_code
        preference.save(update_fields=['language_code', 'updated_at'])
    return preference


def is_subscribed_to_litter(*, user, litter) -> bool:
    try:
        override_record = LitterAlertOverride.objects.get(
            user=user,
            litter=litter,
        )
    except LitterAlertOverride.DoesNotExist:
        override_record = None
    if override_record is not None:
        return override_record.enabled

    try:
        preference = user.litter_alert_preference
    except LitterAlertPreference.DoesNotExist:
        return False
    if preference.scope == LitterAlertPreference.Scope.ALL:
        return True
    if preference.scope == LitterAlertPreference.Scope.SELECTED_BREEDS:
        return preference.breeds.filter(pk=litter.breed_id).exists()
    return False


@transaction.atomic
def set_litter_subscription(
    *,
    user,
    litter,
    enabled: bool,
    language_code: str,
):
    get_or_create_alert_preference(
        user,
        language_code=language_code,
    )
    override_record, _ = LitterAlertOverride.objects.update_or_create(
        user=user,
        litter=litter,
        defaults={'enabled': enabled},
    )
    if not enabled:
        LitterBirthNotification.objects.filter(
            user=user,
            announcement__litter=litter,
            status__in=(
                LitterBirthNotification.Status.PENDING,
                LitterBirthNotification.Status.FAILED,
            ),
        ).update(
            status=LitterBirthNotification.Status.CANCELLED,
            next_retry_at=None,
        )
    return override_record


@transaction.atomic
def queue_birth_announcement(litter_id: int):
    litter = (
        Litter.objects.select_for_update()
        .select_related('breed')
        .get(pk=litter_id)
    )
    if (
        litter.status == Litter.LitterStatus.EXPECTING
        or not litter.birth_date
        or not litter.babies
    ):
        return None

    announcement, created = LitterBirthAnnouncement.objects.get_or_create(
        litter=litter,
        defaults={
            'litter_name': litter.name,
            'breed_name': litter.breed.name,
            'babies': litter.babies,
            'birth_date': litter.birth_date,
        },
    )
    if not created:
        return announcement

    candidates = (
        User.objects.filter(is_active=True)
        .exclude(email='')
        .filter(
            Q(
                litter_alert_overrides__litter=litter,
                litter_alert_overrides__enabled=True,
            )
            | Q(
                litter_alert_preference__scope=(
                    LitterAlertPreference.Scope.ALL
                ),
            )
            | Q(
                litter_alert_preference__scope=(
                    LitterAlertPreference.Scope.SELECTED_BREEDS
                ),
                litter_alert_preference__breeds=litter.breed,
            )
        )
        .exclude(
            litter_alert_overrides__litter=litter,
            litter_alert_overrides__enabled=False,
        )
        .select_related('litter_alert_preference')
        .distinct()
    )
    notifications = []
    for user in candidates:
        try:
            preference = user.litter_alert_preference
        except LitterAlertPreference.DoesNotExist:
            preference = None
        notifications.append(
            LitterBirthNotification(
                announcement=announcement,
                user=user,
                recipient=user.email,
                language_code=(
                    preference.language_code
                    if preference
                    else settings.LANGUAGE_CODE
                ),
                status=LitterBirthNotification.Status.PENDING,
                next_retry_at=timezone.now(),
            )
        )
    LitterBirthNotification.objects.bulk_create(
        notifications,
        ignore_conflicts=True,
    )
    return announcement


def process_birth_notification(notification_id: int):
    notification = _claim_notification(notification_id)
    if notification is None:
        return LitterBirthNotification.objects.get(pk=notification_id)

    announcement = notification.announcement
    litter = announcement.litter
    if litter is None or not is_subscribed_to_litter(
        user=notification.user,
        litter=litter,
    ):
        return _cancel_notification(notification.pk)

    with override(notification.language_code):
        subject = _('%(litter)s has been born') % {
            'litter': announcement.litter_name,
        }
        litter_url = _litter_url(
            litter,
            language_code=notification.language_code,
        )
        content = _litter_birth_email(
            notification=notification,
            announcement=announcement,
            litter=litter,
            subject=subject,
            litter_url=litter_url,
        )
    try:
        send_branded_email(
            content=content,
            language_code=notification.language_code,
            recipients=[notification.recipient],
        )
    except Exception as exc:
        return _record_notification_failure(notification.pk, exc)
    return _record_notification_success(notification.pk)


@transaction.atomic
def _claim_notification(notification_id: int):
    notification = (
        LitterBirthNotification.objects.select_for_update()
        .select_related('announcement__litter', 'user')
        .get(pk=notification_id)
    )
    now = timezone.now()
    if notification.status in {
        LitterBirthNotification.Status.SENT,
        LitterBirthNotification.Status.CANCELLED,
    }:
        return None
    if (
        notification.status == LitterBirthNotification.Status.PROCESSING
        and notification.processing_started_at
        and notification.processing_started_at > now - PROCESSING_LEASE
    ):
        return None

    notification.status = LitterBirthNotification.Status.PROCESSING
    notification.processing_started_at = now
    notification.attempt_count += 1
    notification.next_retry_at = None
    notification.last_error = ''
    notification.save(
        update_fields=[
            'status',
            'processing_started_at',
            'attempt_count',
            'next_retry_at',
            'last_error',
            'updated_at',
        ],
    )
    return notification


@transaction.atomic
def _record_notification_success(notification_id: int):
    notification = LitterBirthNotification.objects.select_for_update().get(
        pk=notification_id,
    )
    notification.status = LitterBirthNotification.Status.SENT
    notification.processing_started_at = None
    notification.next_retry_at = None
    notification.last_error = ''
    notification.sent_at = timezone.now()
    notification.save(
        update_fields=[
            'status',
            'processing_started_at',
            'next_retry_at',
            'last_error',
            'sent_at',
            'updated_at',
        ],
    )
    return notification


@transaction.atomic
def _record_notification_failure(notification_id: int, exc):
    notification = LitterBirthNotification.objects.select_for_update().get(
        pk=notification_id,
    )
    notification.status = LitterBirthNotification.Status.FAILED
    notification.processing_started_at = None
    notification.last_error = (
        f'{exc.__class__.__name__}: {str(exc)}'
    )[:2000]
    if (
        notification.attempt_count
        < settings.LITTER_ALERT_MAX_AUTOMATIC_ATTEMPTS
    ):
        notification.next_retry_at = timezone.now() + timedelta(
            minutes=min(2 ** notification.attempt_count, 60),
        )
    else:
        notification.next_retry_at = None
    notification.save(
        update_fields=[
            'status',
            'processing_started_at',
            'last_error',
            'next_retry_at',
            'updated_at',
        ],
    )
    logger.exception(
        'Unable to send litter birth notification',
        extra={'birth_notification_id': notification.pk},
    )
    return notification


@transaction.atomic
def _cancel_notification(notification_id: int):
    notification = LitterBirthNotification.objects.select_for_update().get(
        pk=notification_id,
    )
    notification.status = LitterBirthNotification.Status.CANCELLED
    notification.processing_started_at = None
    notification.next_retry_at = None
    notification.save(
        update_fields=[
            'status',
            'processing_started_at',
            'next_retry_at',
            'updated_at',
        ],
    )
    return notification


def _litter_url(litter, *, language_code):
    return absolute_reverse(
        'breeding:litter_detail',
        args=[litter.pk],
        language_code=language_code,
    )


def _litter_birth_email(
    *,
    notification,
    announcement,
    litter,
    subject,
    litter_url,
):
    intro = ngettext(
        'The %(litter)s litter (%(breed)s) has been born with one baby.',
        'The %(litter)s litter (%(breed)s) has been born with %(babies)d '
        'babies.',
        announcement.babies,
    ) % {
        'litter': announcement.litter_name,
        'breed': announcement.breed_name,
        'babies': announcement.babies,
    }
    image_url = ''
    cover = litter.cover
    if cover is not None:
        try:
            image_url = absolute_url(cover.file.url)
        except (AttributeError, ValueError):
            image_url = ''

    return BrandedEmailContent(
        subject=subject,
        title=_('The litter has been born'),
        preheader=intro,
        eyebrow=_('Birth announcement'),
        intro=intro,
        recipient_name=(
            notification.user.get_full_name()
            or notification.user.get_username()
        ),
        status_label=_('Born'),
        tone='success',
        details=(
            EmailDetail(_('Litter'), announcement.litter_name),
            EmailDetail(_('Breed'), announcement.breed_name),
            EmailDetail(
                _('Babies born'),
                str(announcement.babies),
                highlight=True,
            ),
            EmailDetail(
                _('Birth date'),
                format_email_date(announcement.birth_date),
            ),
        ),
        notice_title=_('Follow their first updates'),
        notice=_(
            'Visit the litter page to follow the publication of the '
            'individual dogs as information and photographs become '
            'available.'
        ),
        primary_action=EmailAction(_('Meet the litter'), litter_url),
        secondary_action=EmailAction(
            _('Manage litter alerts'),
            absolute_reverse(
                'litter_alert_settings',
                language_code=notification.language_code,
            ),
        ),
        target_name=announcement.litter_name,
        target_breed=announcement.breed_name,
        target_image_url=image_url,
        target_url=litter_url,
        footer_note=_(
            'You can stop alerts for this litter from its page, or manage '
            'all birth alerts from your profile.'
        ),
    )
