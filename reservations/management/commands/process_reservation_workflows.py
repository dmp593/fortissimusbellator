import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from reservations.models import ERPDocument, Payment
from reservations.services.erp import ensure_erp_pdf_and_email, process_erp_document
from reservations.services.payment import (
    process_refund,
    reconcile_pending_payment,
)


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
            reservation__hold_expires_at__lte=now,
        ).values_list('pk', flat=True)[:limit]
        for payment_id in pending_payments:
            processed += self._run(
                'payment reconciliation',
                payment_id,
                reconcile_pending_payment,
            )

        refund_payments = Payment.objects.filter(
            status__in=(Payment.Status.REFUND_PENDING, Payment.Status.REFUND_FAILED),
        ).filter(
            Q(refund_next_retry_at__isnull=True)
            | Q(refund_next_retry_at__lte=now)
        ).values_list('pk', flat=True)[:limit]
        for payment_id in refund_payments:
            processed += self._run('refund', payment_id, process_refund)

        erp_documents = ERPDocument.objects.filter(
            Q(status=ERPDocument.Status.PENDING)
            | Q(
                status=ERPDocument.Status.RETRYABLE_FAILURE,
                next_retry_at__lte=now,
            )
            | Q(
                status=ERPDocument.Status.PROCESSING,
                processing_started_at__lt=now - timedelta(minutes=10),
            )
        ).order_by('created_at').values_list('pk', flat=True)[:limit]
        for document_id in erp_documents:
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
