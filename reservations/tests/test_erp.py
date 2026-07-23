from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings

from reservations.models import ERPDocument, Payment, PreReservation
from reservations.services.erp import download_erp_pdf, process_erp_document
from reservations.services.reservation import ensure_sale_erp_document
from reservations.tests.base import ReservationTestMixin


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    TOCONLINE_ENABLED=True,
)
class ERPWorkflowTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()
        self.reservation = self.reserve(self.dog)
        self.reservation.status = PreReservation.Status.CONFIRMED
        self.reservation.save(update_fields=['status'])
        Payment.objects.filter(reservation=self.reservation).update(
            status=Payment.Status.PAID,
            stripe_payment_intent_id='pi_paid',
        )
        self.document = ensure_sale_erp_document(self.reservation)

    @staticmethod
    def print_url(*, host='files.toconline.pt'):
        return SimpleNamespace(
            data=SimpleNamespace(
                attributes=SimpleNamespace(
                    url=SimpleNamespace(
                        scheme='https',
                        host=host,
                        port=443,
                        path='/public-file/signed-document',
                    )
                )
            )
        )

    @staticmethod
    def pdf_response(content):
        response = Mock()
        response.headers = {'Content-Length': str(len(content))}
        response.iter_content.return_value = [content]
        return response

    def test_creates_document_with_the_v2_typed_sales_api(self):
        created = {
            'data': {
                'id': 'erp-123',
                'attributes': {
                    'document_series_prefix': 'FR 2026',
                    'document_no': 1,
                },
            }
        }
        pdf = b'%PDF-1.7\nexample'
        with patch(
            'toconline.services.toconline.api.sales.create_sales_document',
            return_value=created,
        ) as create, patch(
            'toconline.services.toconline.api.documents.get_sales_document_print_url',
            return_value=self.print_url(),
        ) as get_print_url, patch(
            'reservations.services.erp.requests.get',
            return_value=self.pdf_response(pdf),
        ):
            document = process_erp_document(self.document.pk)

        body = create.call_args.kwargs['body']
        self.assertEqual(document.status, ERPDocument.Status.INTEGRATED)
        self.assertEqual(document.erp_document_id, 'erp-123')
        self.assertEqual(document.erp_document_number, 'FR 2026/1')
        self.assertEqual(document.pdf_status, ERPDocument.PDFStatus.AVAILABLE)
        self.assertFalse(document.creation_uncertain)
        self.assertEqual(body.external_reference, document.external_reference)
        self.assertEqual(body.document_type, 'FR')
        get_print_url.assert_called_once_with('erp-123')

    def test_uncertain_creation_reconciles_without_creating_a_second_document(self):
        self.document.creation_uncertain = True
        self.document.save(update_fields=['creation_uncertain'])
        existing = {
            'id': 'erp-123',
            'attributes': {
                'external_reference': self.document.external_reference,
                'document_number': 'FR 2026/1',
            },
        }
        with patch(
            'toconline.services.toconline.api.sales.list_sales_documents',
            return_value={'data': [existing]},
        ) as list_documents, patch(
            'toconline.services.toconline.api.sales.create_sales_document'
        ) as create, patch(
            'reservations.services.erp._download_sales_document_pdf',
            return_value=b'%PDF-1.7\nexample',
        ):
            document = process_erp_document(self.document.pk)

        self.assertEqual(document.status, ERPDocument.Status.INTEGRATED)
        self.assertEqual(document.erp_document_id, 'erp-123')
        self.assertFalse(document.creation_uncertain)
        list_documents.assert_called_once_with(
            params={'filter[external_reference]': document.external_reference}
        )
        create.assert_not_called()
        self.assertEqual(
            document.integration_attempts.get().result,
            'reconciled',
        )

    def test_ambiguous_creation_failure_needs_attention_without_retrying_create(self):
        with patch(
            'toconline.services.toconline.api.sales.create_sales_document',
            side_effect=requests.ConnectionError('TOConline unavailable'),
        ), patch(
            'toconline.services.toconline.api.sales.list_sales_documents',
            return_value={'data': []},
        ):
            document = process_erp_document(self.document.pk)

        self.reservation.refresh_from_db()
        self.reservation.payment.refresh_from_db()
        self.assertEqual(self.reservation.status, PreReservation.Status.CONFIRMED)
        self.assertEqual(self.reservation.payment.status, Payment.Status.PAID)
        self.assertEqual(document.status, ERPDocument.Status.NEEDS_ATTENTION)
        self.assertTrue(document.creation_uncertain)
        self.assertIsNotNone(document.creation_started_at)
        self.assertIsNone(document.next_retry_at)

    def test_pdf_failure_does_not_change_integrated_sale_or_payment(self):
        self.document.status = ERPDocument.Status.INTEGRATED
        self.document.erp_document_id = 'erp-456'
        self.document.save(update_fields=['status', 'erp_document_id'])

        with patch(
            'toconline.services.toconline.api.documents.get_sales_document_print_url',
            side_effect=requests.ConnectionError('PDF unavailable'),
        ):
            document = download_erp_pdf(self.document.pk)

        self.reservation.payment.refresh_from_db()
        self.assertEqual(document.status, ERPDocument.Status.INTEGRATED)
        self.assertEqual(document.pdf_status, ERPDocument.PDFStatus.FAILED)
        self.assertEqual(self.reservation.payment.status, Payment.Status.PAID)

    def test_invalid_pdf_is_not_stored(self):
        self.document.status = ERPDocument.Status.INTEGRATED
        self.document.erp_document_id = 'erp-789'
        self.document.save(update_fields=['status', 'erp_document_id'])

        with patch(
            'toconline.services.toconline.api.documents.get_sales_document_print_url',
            return_value=self.print_url(),
        ), patch(
            'reservations.services.erp.requests.get',
            return_value=self.pdf_response(b'<html>not a pdf</html>'),
        ):
            document = download_erp_pdf(self.document.pk)

        self.assertEqual(document.pdf_status, ERPDocument.PDFStatus.FAILED)
        self.assertIsNone(document.pdf_data)

    def test_untrusted_print_url_is_not_requested(self):
        self.document.status = ERPDocument.Status.INTEGRATED
        self.document.erp_document_id = 'erp-999'
        self.document.save(update_fields=['status', 'erp_document_id'])

        with patch(
            'toconline.services.toconline.api.documents.get_sales_document_print_url',
            return_value=self.print_url(host='untrusted.example'),
        ), patch('reservations.services.erp.requests.get') as get:
            document = download_erp_pdf(self.document.pk)

        self.assertEqual(document.pdf_status, ERPDocument.PDFStatus.FAILED)
        get.assert_not_called()
