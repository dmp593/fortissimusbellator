import logging

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import override

from reservations.models import DocumentEmailAttempt


logger = logging.getLogger(__name__)


def notify_payment_confirmed(reservation):
    context = {'reservation': reservation}
    with override(reservation.language_code):
        subject = f'Pre-reservation confirmed: {reservation.target_name}'
        body = render_to_string(
            'reservations/emails/payment_confirmed.txt',
            context,
        )
    _send_mail_safely(
        subject=subject,
        body=body,
        recipients=[reservation.customer_email],
        log_message='Unable to send customer payment confirmation email',
        reservation=reservation,
    )
    _send_mail_safely(
        subject=f'Paid pre-reservation: {reservation.target_name}',
        body=body,
        recipients=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
        log_message='Unable to send business payment notification email',
        reservation=reservation,
    )


def notify_reservation_cancelled(reservation, *, cancelled_by_staff: bool):
    context = {
        'reservation': reservation,
        'cancelled_by_staff': cancelled_by_staff,
    }
    with override(reservation.language_code):
        subject = f'Pre-reservation cancelled: {reservation.target_name}'
        body = render_to_string(
            'reservations/emails/reservation_cancelled.txt',
            context,
        )
    _send_mail_safely(
        subject=subject,
        body=body,
        recipients=[reservation.customer_email],
        log_message='Unable to send customer cancellation email',
        reservation=reservation,
    )
    _send_mail_safely(
        subject=f'Cancelled pre-reservation: {reservation.target_name}',
        body=body,
        recipients=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
        log_message='Unable to send business cancellation notification email',
        reservation=reservation,
    )


def notify_late_payment_refund_queued(reservation):
    subject = f'Payment received after closure: {reservation.target_name}'
    body = (
        f'Payment for pre-reservation {reservation.public_id} arrived after the '
        'reservation had already closed. An automatic refund has been queued. '
        'Our team can monitor the refund from the reservation admin dashboard.'
    )
    _send_mail_safely(
        subject=subject,
        body=body,
        recipients=[reservation.customer_email],
        log_message='Unable to send late-payment customer notification',
        reservation=reservation,
    )
    _send_mail_safely(
        subject=subject,
        body=body,
        recipients=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
        log_message='Unable to send late-payment business notification',
        reservation=reservation,
    )


def _send_mail_safely(*, subject, body, recipients, log_message, reservation):
    if not recipients:
        return
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            log_message,
            extra={'reservation_id': str(reservation.public_id)},
        )


def notify_erp_needs_attention(document):
    try:
        send_mail(
            subject=f'ERP integration needs attention: {document.external_reference}',
            message=(
                f'Pre-reservation {document.reservation.public_id} was paid but '
                f'its ERP document is not integrated.\n\n'
                f'Last error: {document.last_error}'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            'Unable to send ERP failure notification',
            extra={'erp_document_id': document.pk},
        )


def send_document_email(*, document, recipient: str, triggered_by=None):
    if not document.pdf_data:
        raise ValueError('The ERP document PDF is not available.')

    reservation = document.reservation
    with override(reservation.language_code):
        subject = f'Fiscal document for {reservation.target_name}'
        body = render_to_string(
            'reservations/emails/fiscal_document.txt',
            {'reservation': reservation, 'document': document},
        )
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    email.attach(
        document.pdf_filename,
        bytes(document.pdf_data),
        'application/pdf',
    )
    try:
        email.send(fail_silently=False)
    except Exception as exc:
        DocumentEmailAttempt.objects.create(
            document=document,
            recipient=recipient,
            status=DocumentEmailAttempt.Status.FAILED,
            triggered_by=triggered_by,
            error_message=f'{exc.__class__.__name__}: {str(exc)}'[:2000],
        )
        raise

    return DocumentEmailAttempt.objects.create(
        document=document,
        recipient=recipient,
        status=DocumentEmailAttempt.Status.SENT,
        triggered_by=triggered_by,
        sent_at=timezone.now(),
    )
