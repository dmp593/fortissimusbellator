import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from breeding.models import LitterBirthNotification
from breeding.services.litter_alerts import process_birth_notification
from reservations.models import (
    ERPDocument,
    Payment,
    PaymentRefund,
    Reservation,
)
from reservations.policies import checkout_duration_minutes
from reservations.services.erp import ensure_erp_pdf_and_email, process_erp_document
from reservations.services.payment import (
    process_refund,
    reconcile_pending_payment,
    refresh_payment_financials,
)
from reservations.services.reservation import expire_reservation_offer


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Reconcile pending Stripe payments and process durable ERP, refund, '
        'and PDF work.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = max(1, options['limit'])
        processed = 0
        now = timezone.now()

        pending_payments = Payment.objects.filter(
            status__in=(Payment.Status.INITIALIZING, Payment.Status.PENDING),
        ).filter(
            Q(stripe_checkout_expires_at__lte=now)
            | Q(pre_reservation__hold_expires_at__lte=now)
            | Q(
                stripe_checkout_session_id__isnull=True,
                created_at__lte=now
                - timedelta(minutes=checkout_duration_minutes() + 10),
            )
        ).values_list('pk', flat=True)[:limit]
        for payment_id in pending_payments:
            processed += self._run(
                'payment reconciliation',
                payment_id,
                reconcile_pending_payment,
            )

        refunds = PaymentRefund.objects.filter(
            Q(
                status__in=(
                    PaymentRefund.Status.PENDING,
                    PaymentRefund.Status.FAILED,
                ),
                next_retry_at__lte=now,
            )
            | Q(
                status=PaymentRefund.Status.PROCESSING,
                processing_started_at__lt=now - timedelta(minutes=10),
            ),
            attempt_count__lt=(
                settings.RESERVATION_REFUND_MAX_AUTOMATIC_ATTEMPTS
            ),
        ).values_list('pk', flat=True)[:limit]
        for refund_id in refunds:
            processed += self._run('refund', refund_id, process_refund)

        financial_payments = Payment.objects.filter(
            status__in=(
                Payment.Status.PAID,
                Payment.Status.PARTIALLY_REFUNDED,
                Payment.Status.REFUNDED,
            ),
            provider=Payment.Provider.STRIPE,
            provider_net_amount__isnull=True,
            financials_attempt_count__lt=5,
        ).filter(
            Q(financials_next_retry_at__isnull=True)
            | Q(financials_next_retry_at__lte=now)
        ).values_list('pk', flat=True)[:limit]
        for payment_id in financial_payments:
            processed += self._run(
                'Stripe financial reconciliation',
                payment_id,
                refresh_payment_financials,
            )

        expired_offers = Reservation.objects.filter(
            status__in=(
                Reservation.Status.OFFERED,
                Reservation.Status.PENDING_PAYMENT,
                Reservation.Status.PAYMENT_FAILED,
            ),
            offer_expires_at__lte=now,
        ).values_list('pk', flat=True)[:limit]
        for reservation_id in expired_offers:
            processed += self._run(
                'reservation offer expiry',
                reservation_id,
                expire_reservation_offer,
            )

        erp_documents = ERPDocument.objects.none()
        if settings.TOCONLINE_ENABLED:
            erp_documents = ERPDocument.objects.filter(
                Q(
                    status__in=(
                        ERPDocument.Status.DEFERRED,
                        ERPDocument.Status.PENDING,
                    )
                )
                | Q(
                    status=ERPDocument.Status.RETRYABLE_FAILURE,
                    next_retry_at__lte=now,
                )
                | Q(
                    status=ERPDocument.Status.PROCESSING,
                    processing_started_at__lt=now - timedelta(minutes=10),
                )
            )
        erp_document_ids = (
            erp_documents.order_by('created_at')
            .values_list('pk', flat=True)[:limit]
        )
        for document_id in erp_document_ids:
            processed += self._run(
                'ERP integration',
                document_id,
                process_erp_document,
            )

        missing_pdfs = ERPDocument.objects.filter(
            status=ERPDocument.Status.INTEGRATED,
            pdf_status__in=(
                ERPDocument.PDFStatus.PENDING,
                ERPDocument.PDFStatus.FAILED,
            ),
            pdf_attempt_count__lt=5,
        ).values_list('pk', flat=True)[:limit]
        for document_id in missing_pdfs:
            processed += self._run(
                'ERP PDF download',
                document_id,
                ensure_erp_pdf_and_email,
            )

        birth_notifications = LitterBirthNotification.objects.filter(
            Q(
                status__in=(
                    LitterBirthNotification.Status.PENDING,
                    LitterBirthNotification.Status.FAILED,
                ),
                next_retry_at__lte=now,
            )
            | Q(
                status=LitterBirthNotification.Status.PROCESSING,
                processing_started_at__lt=now - timedelta(minutes=10),
            ),
            attempt_count__lt=settings.LITTER_ALERT_MAX_AUTOMATIC_ATTEMPTS,
        ).values_list('pk', flat=True)[:limit]
        for notification_id in birth_notifications:
            processed += self._run(
                'litter birth notification',
                notification_id,
                process_birth_notification,
            )

        self.stdout.write(self.style.SUCCESS(f'Processed {processed} work item(s).'))

    def _run(self, name, object_id, operation):
        try:
            operation(object_id)
        except Exception:
            logger.exception(
                'Reservation workflow operation failed',
                extra={'operation': name, 'object_id': object_id},
            )
            self.stderr.write(f'{name} failed for ID {object_id}.')
        return 1
