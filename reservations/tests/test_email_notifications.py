import decimal
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from fortissimusbellator.business import CONTACT_EMAIL
from fortissimusbellator.emails import send_branded_email
from reservations.models import (
    AnimalSale,
    AnimalSaleCase,
    AnimalWorkflowTransfer,
    Charge,
    DocumentEmailAttempt,
    ERPDocument,
    Payment,
    PaymentRefund,
    Reservation,
    WorkflowClosure,
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
from reservations.services.notifications import (
    notify_pre_reservation_paid,
    send_document_email,
)
from reservations.services.payment import release_failed_or_expired_checkout
from reservations.services.reservation import expire_reservation_offer
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    BUSINESS_NOTIFICATION_RECIPIENTS=['staff@example.com'],
    PUBLIC_SITE_URL='https://fortissimusbellator.test',
    STATIC_ROOT=None,
    STORAGES=TEST_STORAGES,
)
class CommercialEmailTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()
        self.pre_reservation = self.reserve(self.dog)
        self.workflow = self.pre_reservation.sale_case
        self.reservation_charge = Charge.objects.create(
            sale_case=self.workflow,
            stage=Charge.Stage.RESERVATION,
            status=Charge.Status.PAID,
            subtotal_amount=decimal.Decimal('750.00'),
            currency='EUR',
        )
        self.reservation = Reservation.objects.create(
            sale_case=self.workflow,
            charge=self.reservation_charge,
            pre_reservation=self.pre_reservation,
            status=Reservation.Status.CONFIRMED,
            pre_reservation_credit_amount=decimal.Decimal('50.00'),
            deposit_target_amount=decimal.Decimal('750.00'),
            payment_amount=decimal.Decimal('700.00'),
            currency='EUR',
            offer_expires_at=timezone.now() + timedelta(hours=72),
            terms=self.reservation_terms,
            terms_accepted_at=timezone.now(),
        )
        self.reservation_payment = Payment.objects.create(
            charge=self.reservation_charge,
            animal_reservation=self.reservation,
            provider=Payment.Provider.BANK_TRANSFER,
            status=Payment.Status.PAID,
            amount=decimal.Decimal('700.00'),
            currency='EUR',
            paid_at=timezone.now(),
        )
        self.pre_closure = WorkflowClosure.objects.create(
            sale_case=self.workflow,
            stage=Charge.Stage.PRE_RESERVATION,
            kind=WorkflowClosure.Kind.CANCELLED,
            paid_value_amount=decimal.Decimal('50.00'),
            refund_amount=decimal.Decimal('10.00'),
            credit_amount=decimal.Decimal('20.00'),
            retained_amount=decimal.Decimal('20.00'),
            reason='Customer request.',
        )
        self.reservation_closure = WorkflowClosure.objects.create(
            sale_case=self.workflow,
            stage=Charge.Stage.RESERVATION,
            kind=WorkflowClosure.Kind.CANCELLED,
            paid_value_amount=decimal.Decimal('750.00'),
            refund_amount=decimal.Decimal('500.00'),
            credit_amount=decimal.Decimal('200.00'),
            retained_amount=decimal.Decimal('50.00'),
            reason='Exceptional cancellation.',
        )
        self.payment_refund = PaymentRefund.objects.create(
            payment=self.reservation_payment,
            closure=self.reservation_closure,
            processing_method=PaymentRefund.ProcessingMethod.MANUAL,
            calculation_type=PaymentRefund.CalculationType.FIXED,
            amount=decimal.Decimal('500.00'),
            reason='Approved cancellation refund.',
            status=PaymentRefund.Status.SUCCEEDED,
            succeeded_at=timezone.now(),
        )

        target_dog = type(self.dog).objects.create(
            breed=self.breed,
            name='Gaia',
            birth_date=self.dog.birth_date,
            active=True,
            for_sale=True,
            price_in_euros='1600.00',
        )
        self.target_workflow = AnimalSaleCase.objects.create(
            user=self.user,
            animal=target_dog,
            origin=AnimalSaleCase.Origin.TRANSFER,
            status=AnimalSaleCase.Status.PRE_RESERVATION,
            target_name=target_dog.name,
            target_breed=self.breed.name,
            target_birth_date=target_dog.birth_date,
            customer_name='Customer Example',
            customer_email=self.user.email,
            customer_phone='+351900000000',
            language_code='en',
            currency='EUR',
        )
        self.target_charge = Charge.objects.create(
            sale_case=self.target_workflow,
            stage=Charge.Stage.PRE_RESERVATION,
            subtotal_amount=decimal.Decimal('60.00'),
            currency='EUR',
        )
        self.transfer = AnimalWorkflowTransfer.objects.create(
            source_case=self.workflow,
            target_case=self.target_workflow,
            source_stage=Charge.Stage.PRE_RESERVATION,
            target_stage=Charge.Stage.PRE_RESERVATION,
            available_value_amount=decimal.Decimal('50.00'),
            transferred_amount=decimal.Decimal('40.00'),
            refund_amount=decimal.Decimal('10.00'),
            retained_amount=decimal.Decimal('0.00'),
            reason='Customer selected another dog.',
        )

        self.sale_charge = Charge.objects.create(
            sale_case=self.workflow,
            stage=Charge.Stage.SALE,
            status=Charge.Status.PAID,
            subtotal_amount=decimal.Decimal('1500.00'),
            currency='EUR',
        )
        self.sale = AnimalSale.objects.create(
            sale_case=self.workflow,
            charge=self.sale_charge,
            final_price=decimal.Decimal('1500.00'),
            sold_at=timezone.localdate(),
        )
        self.document = ERPDocument.objects.create(
            charge=self.sale_charge,
            kind=ERPDocument.Kind.SALE,
            amount=decimal.Decimal('1500.00'),
            currency='EUR',
            status=ERPDocument.Status.INTEGRATED,
            external_reference='sale-email-test',
            erp_document_number='FT 2026/10',
            pdf_status=ERPDocument.PDFStatus.AVAILABLE,
            pdf_data=b'%PDF-1.4 test',
            pdf_filename='invoice.pdf',
        )

    def test_every_commercial_email_renders_branded_html_and_text(self):
        builders = [
            pre_reservation_payment_requested_email(self.pre_reservation),
            reservation_payment_requested_email(self.reservation),
            pre_reservation_paid_email(self.pre_reservation),
            pre_reservation_accepted_email(self.pre_reservation),
            reservation_confirmed_email(self.reservation),
            reservation_cancelled_email(self.reservation),
            pre_reservation_closed_email(
                self.pre_reservation,
                rejected=False,
                cancelled_by_staff=True,
            ),
            late_payment_refund_email(self.pre_reservation),
            refund_succeeded_email(self.payment_refund),
            workflow_transferred_email(self.transfer),
            animal_sale_completed_email(self.sale),
            animal_sale_cancelled_email(self.sale),
            fiscal_document_email(self.document),
            payment_failed_email(
                self.reservation,
                expired=False,
            ),
            reservation_offer_expired_email(self.reservation),
            erp_needs_attention_email(self.document),
        ]

        for content in builders:
            with self.subTest(subject=content.subject):
                mail.outbox.clear()
                send_branded_email(
                    content=content,
                    language_code='en',
                    recipients=['recipient@example.com'],
                )

                message = mail.outbox[0]
                html = message.alternatives[0][0]
                self.assertEqual(message.alternatives[0][1], 'text/html')
                self.assertEqual(message.reply_to, [CONTACT_EMAIL])
                self.assertIn('Fortissimus Bellator', message.body)
                self.assertIn('Fortissimus Bellator', html)
                self.assertIn('https://fortissimusbellator.test/', html)
                self.assertIn('geral@fortissimusbellator.pt', html)
                self.assertIn('name="viewport"', html)
                self.assertIn('@media only screen and (max-width: 680px)', html)
                self.assertIn('role="presentation"', html)
                self.assertIn('mailto:geral@fortissimusbellator.pt', html)
                self.assertIn('https://wa.me/351924454382', html)
                self.assertNotIn('{%', html)
                self.assertNotIn('{{', html)

    def test_customer_email_renders_in_every_supported_language(self):
        expected_translations = {
            'pt': (
                'Pagamento de pré-reserva recebido: Athena',
                'Pagamento de pré-reserva recebido',
            ),
            'es': (
                'Pago de pre-reserva recibido: Athena',
                'Pago de pre-reserva recibido',
            ),
            'fr': (
                'Paiement de pré-réservation reçu : Athena',
                'Pré-réservation payée',
            ),
            'de': (
                'Vorreservierungszahlung erhalten: Athena',
                'Vorreservierungszahlung erhalten',
            ),
            'it': (
                'Pagamento della pre-prenotazione ricevuto: Athena',
                'Pagamento della pre-prenotazione ricevuto',
            ),
        }

        for language_code, expected in expected_translations.items():
            with self.subTest(language_code=language_code):
                mail.outbox.clear()
                content = pre_reservation_paid_email(
                    self.pre_reservation,
                    language_code=language_code,
                )
                send_branded_email(
                    content=content,
                    language_code=language_code,
                    recipients=['recipient@example.com'],
                )

                message = mail.outbox[0]
                html = message.alternatives[0][0]
                expected_subject, expected_title = expected
                self.assertEqual(message.subject, expected_subject)
                self.assertIn(expected_title, html)
                self.assertIn(
                    f'https://fortissimusbellator.test/{language_code}/',
                    html,
                )

    def test_staff_payment_request_subject_is_translated(self):
        expected_subjects = {
            'pt': 'Conclua a sua pré-reserva: Athena',
            'es': 'Complete su pre-reserva: Athena',
            'fr': 'Finalisez votre pré-réservation : Athena',
            'de': 'Schließen Sie Ihre Vorreservierung ab: Athena',
            'it': 'Completa la tua pre-prenotazione: Athena',
        }

        for language_code, expected_subject in expected_subjects.items():
            with self.subTest(language_code=language_code):
                content = pre_reservation_payment_requested_email(
                    self.pre_reservation,
                    language_code=language_code,
                )
                self.assertEqual(content.subject, expected_subject)

    def test_customer_and_business_receive_context_appropriate_actions(self):
        notify_pre_reservation_paid(self.pre_reservation)

        self.assertEqual(len(mail.outbox), 2)
        customer_html = mail.outbox[0].alternatives[0][0]
        business_html = mail.outbox[1].alternatives[0][0]
        self.assertIn('/en/my-reservations/', customer_html)
        self.assertNotIn('Internal commercial notification', customer_html)
        self.assertIn('Internal commercial notification', business_html)
        self.assertIn(
            (
                '/en/admin/reservations/animalsalecase/'
                f'{self.workflow.pk}/change/'
            ),
            business_html,
        )

    def test_fiscal_document_email_is_html_and_keeps_pdf_attachment(self):
        attempt = send_document_email(
            document=self.document,
            recipient=self.user.email,
        )

        self.assertEqual(attempt.status, DocumentEmailAttempt.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.alternatives[0][1], 'text/html')
        self.assertEqual(message.attachments[0][0], 'invoice.pdf')
        self.assertEqual(message.attachments[0][2], 'application/pdf')

    def test_failed_checkout_sends_customer_and_business_email(self):
        self.reservation.status = Reservation.Status.PENDING_PAYMENT
        self.reservation.save(update_fields=['status', 'updated_at'])
        self.reservation_payment.status = Payment.Status.PENDING
        self.reservation_payment.stripe_checkout_session_id = 'cs_failed_email'
        self.reservation_payment.save(
            update_fields=[
                'status',
                'stripe_checkout_session_id',
                'updated_at',
            ],
        )

        with self.captureOnCommitCallbacks(execute=True):
            release_failed_or_expired_checkout(
                session_id='cs_failed_email',
                expired=False,
            )

        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(
            all('Payment not completed' in message.subject for message in mail.outbox)
        )

    def test_offer_expiry_sends_customer_and_business_email(self):
        self.reservation.status = Reservation.Status.OFFERED
        self.reservation.offer_expires_at = timezone.now() - timedelta(minutes=1)
        self.reservation.save(
            update_fields=['status', 'offer_expires_at', 'updated_at'],
        )

        with self.captureOnCommitCallbacks(execute=True):
            expire_reservation_offer(self.reservation.pk)

        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(
            all(
                'Reservation offer expired' in message.subject
                for message in mail.outbox
            )
        )
