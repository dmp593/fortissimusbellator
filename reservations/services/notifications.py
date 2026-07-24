import logging

from django.conf import settings
from django.utils import timezone

from fortissimusbellator.emails import send_branded_email
from reservations.models import (
    AnimalSaleCase,
    DocumentEmailAttempt,
    PreReservation,
)
from reservations.services.email_messages import (
    animal_sale_cancelled_email,
    animal_sale_completed_email,
    erp_needs_attention_email,
    fiscal_document_email,
    late_payment_refund_email,
    payment_failed_email,
    pre_reservation_accepted_email,
    pre_reservation_closed_email,
    pre_reservation_payment_requested_email,
    pre_reservation_paid_email,
    refund_succeeded_email,
    reservation_cancelled_email,
    reservation_confirmed_email,
    reservation_payment_requested_email,
    reservation_offer_expired_email,
    workflow_transferred_email,
)


logger = logging.getLogger(__name__)


def notify_pre_reservation_payment_requested(pre_reservation):
    _send_customer_and_business(
        builder=pre_reservation_payment_requested_email,
        source=pre_reservation,
        workflow=pre_reservation.sale_case or pre_reservation,
        customer_email=pre_reservation.customer_email,
        log_name='staff pre-reservation payment request',
        reference=pre_reservation.public_id,
    )


def notify_reservation_payment_requested(reservation):
    workflow = _purchase_workflow(reservation)
    _send_customer_and_business(
        builder=reservation_payment_requested_email,
        source=reservation,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='staff reservation payment request',
        reference=reservation.public_id,
    )


def notify_pre_reservation_paid(pre_reservation):
    if (
        pre_reservation.sale_case_id
        and pre_reservation.sale_case.origin == AnimalSaleCase.Origin.ADMIN
        and pre_reservation.status
        in {
            PreReservation.Status.ACCEPTED,
            PreReservation.Status.CONVERTED_TO_RESERVATION,
        }
    ):
        return
    _send_customer_and_business(
        builder=pre_reservation_paid_email,
        source=pre_reservation,
        workflow=pre_reservation.sale_case or pre_reservation,
        customer_email=pre_reservation.customer_email,
        log_name='pre-reservation payment',
        reference=pre_reservation.public_id,
    )


def notify_pre_reservation_accepted(pre_reservation):
    _send_customer_and_business(
        builder=pre_reservation_accepted_email,
        source=pre_reservation,
        workflow=pre_reservation.sale_case or pre_reservation,
        customer_email=pre_reservation.customer_email,
        log_name='pre-reservation acceptance',
        reference=pre_reservation.public_id,
    )


def notify_reservation_confirmed(reservation):
    workflow = _purchase_workflow(reservation)
    _send_customer_and_business(
        builder=reservation_confirmed_email,
        source=reservation,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='reservation confirmation',
        reference=reservation.public_id,
    )


def notify_reservation_cancelled(reservation):
    workflow = _purchase_workflow(reservation)
    _send_customer_and_business(
        builder=reservation_cancelled_email,
        source=reservation,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='reservation cancellation',
        reference=reservation.public_id,
    )


def notify_pre_reservation_closed(
    pre_reservation,
    *,
    rejected: bool,
    cancelled_by_staff: bool,
):
    _send_customer_and_business(
        builder=pre_reservation_closed_email,
        source=pre_reservation,
        workflow=pre_reservation.sale_case or pre_reservation,
        customer_email=pre_reservation.customer_email,
        log_name='pre-reservation closure',
        reference=pre_reservation.public_id,
        builder_kwargs={
            'rejected': rejected,
            'cancelled_by_staff': cancelled_by_staff,
        },
    )


def notify_late_payment_refund_queued(purchase):
    workflow = _purchase_workflow(purchase)
    _send_customer_and_business(
        builder=late_payment_refund_email,
        source=purchase,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='late-payment safety refund',
        reference=purchase.public_id,
    )


def notify_refund_succeeded(payment_refund):
    workflow = _purchase_workflow(payment_refund.payment.purchase)
    _send_customer_and_business(
        builder=refund_succeeded_email,
        source=payment_refund,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='successful refund',
        reference=payment_refund.public_id,
    )


def notify_workflow_transferred(workflow_transfer):
    workflow = workflow_transfer.target_case
    _send_customer_and_business(
        builder=workflow_transferred_email,
        source=workflow_transfer,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='animal workflow transfer',
        reference=workflow_transfer.public_id,
    )


def notify_animal_sale_completed(animal_sale):
    workflow = animal_sale.sale_case
    _send_customer_and_business(
        builder=animal_sale_completed_email,
        source=animal_sale,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='completed animal sale',
        reference=animal_sale.public_id,
    )


def notify_animal_sale_cancelled(animal_sale):
    workflow = animal_sale.sale_case
    _send_customer_and_business(
        builder=animal_sale_cancelled_email,
        source=animal_sale,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='cancelled animal sale',
        reference=animal_sale.public_id,
    )


def notify_payment_failed(purchase, *, expired):
    workflow = _purchase_workflow(purchase)
    _send_customer_and_business(
        builder=payment_failed_email,
        source=purchase,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='failed payment',
        reference=purchase.public_id,
        builder_kwargs={'expired': expired},
    )


def notify_reservation_offer_expired(reservation):
    workflow = _purchase_workflow(reservation)
    _send_customer_and_business(
        builder=reservation_offer_expired_email,
        source=reservation,
        workflow=workflow,
        customer_email=workflow.customer_email,
        log_name='reservation offer expiry',
        reference=reservation.public_id,
    )


def notify_erp_needs_attention(document):
    content = erp_needs_attention_email(
        document,
        language_code=settings.LANGUAGE_CODE,
    )
    _send_mail_safely(
        content=content,
        language_code=settings.LANGUAGE_CODE,
        recipients=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
        log_message='Unable to send ERP failure notification',
        reference=document.external_reference,
    )


def send_document_email(*, document, recipient: str, triggered_by=None):
    if not document.pdf_data:
        raise ValueError('The ERP document PDF is not available.')

    workflow = _purchase_workflow(document.purchase)
    content = fiscal_document_email(
        document,
        language_code=workflow.language_code,
    )
    try:
        send_branded_email(
            content=content,
            language_code=workflow.language_code,
            recipients=[recipient],
            attachments=[
                (
                    document.pdf_filename,
                    bytes(document.pdf_data),
                    'application/pdf',
                )
            ],
        )
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


def _send_customer_and_business(
    *,
    builder,
    source,
    workflow,
    customer_email,
    log_name,
    reference,
    builder_kwargs=None,
):
    builder_kwargs = builder_kwargs or {}
    if customer_email:
        customer_content = builder(
            source,
            internal=False,
            language_code=workflow.language_code,
            **builder_kwargs,
        )
        _send_mail_safely(
            content=customer_content,
            language_code=workflow.language_code,
            recipients=[customer_email],
            log_message=f'Unable to send customer {log_name} email',
            reference=reference,
        )

    business_content = builder(
        source,
        internal=True,
        language_code=settings.LANGUAGE_CODE,
        **builder_kwargs,
    )
    _send_mail_safely(
        content=business_content,
        language_code=settings.LANGUAGE_CODE,
        recipients=settings.BUSINESS_NOTIFICATION_RECIPIENTS,
        log_message=f'Unable to send business {log_name} email',
        reference=reference,
    )


def _send_mail_safely(
    *,
    content,
    language_code,
    recipients,
    log_message,
    reference,
):
    if not recipients:
        return
    try:
        send_branded_email(
            content=content,
            language_code=language_code,
            recipients=recipients,
        )
    except Exception:
        logger.exception(
            log_message,
            extra={'workflow_reference': str(reference)},
        )


def _purchase_workflow(purchase):
    if purchase is None:
        return None
    if purchase.__class__.__name__ == 'PreReservation':
        return purchase.sale_case or purchase
    workflow = getattr(purchase, 'workflow', None)
    if workflow is not None:
        return workflow
    sale_case = getattr(purchase, 'sale_case', None)
    if sale_case is not None:
        return sale_case
    charge = getattr(purchase, 'charge', None)
    return charge.sale_case if charge is not None else None
