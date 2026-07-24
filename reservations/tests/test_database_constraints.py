import decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from reservations.models import (
    AnimalSaleCase,
    ERPDocument,
    PreReservation,
)
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class CrossDatabaseConstraintTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()

    def test_only_one_active_sale_case_can_hold_a_dog(self):
        first = self.reserve(self.dog)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_sale_case()

        first.sale_case.status = AnimalSaleCase.Status.CLOSED
        first.sale_case.save(update_fields=['status', 'updated_at'])
        replacement = self._create_sale_case()

        self.assertEqual(replacement.blocking_animal_key, self.dog.pk)

    def test_sold_sale_case_also_blocks_a_second_process(self):
        sold_case = self._create_sale_case(
            status=AnimalSaleCase.Status.SOLD,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_sale_case()

        self.assertEqual(sold_case.blocking_animal_key, self.dog.pk)

    def test_only_one_active_pre_reservation_can_hold_a_dog(self):
        first = self.reserve(self.dog)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_pre_reservation()

        first.status = PreReservation.Status.CANCELLED_BY_ADMIN
        first.save(update_fields=['status', 'updated_at'])
        replacement = self._create_pre_reservation()

        self.assertEqual(replacement.active_animal_key, self.dog.pk)

    def test_sale_erp_document_is_unique_per_charge_and_payment(self):
        pre_reservation = self.reserve(self.dog)
        document = ERPDocument.objects.create(
            payment=pre_reservation.payment,
            charge=pre_reservation.charge,
            kind=ERPDocument.Kind.SALE,
            amount=pre_reservation.total_amount,
            currency='EUR',
            external_reference='original-sale',
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ERPDocument.objects.create(
                payment=pre_reservation.payment,
                kind=ERPDocument.Kind.SALE,
                amount=pre_reservation.total_amount,
                currency='EUR',
                external_reference='duplicate-payment-sale',
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ERPDocument.objects.create(
                charge=pre_reservation.charge,
                kind=ERPDocument.Kind.SALE,
                amount=pre_reservation.total_amount,
                currency='EUR',
                external_reference='duplicate-charge-sale',
            )

        self.assertEqual(document.sale_payment_key, pre_reservation.payment.pk)
        self.assertEqual(document.sale_charge_key, pre_reservation.charge.pk)

    def _create_sale_case(
        self,
        *,
        status=AnimalSaleCase.Status.PRE_RESERVATION,
    ):
        return AnimalSaleCase.objects.create(
            user=self.other_user,
            animal=self.dog,
            status=status,
            target_name=self.dog.name,
            target_breed=self.breed.name,
            customer_name='Other Customer',
            customer_email=self.other_user.email,
        )

    def _create_pre_reservation(self):
        return PreReservation.objects.create(
            user=self.other_user,
            target_type=PreReservation.TargetType.DOG,
            animal=self.dog,
            status=PreReservation.Status.PENDING_PAYMENT,
            target_name=self.dog.name,
            target_breed=self.breed.name,
            target_birth_date=self.dog.birth_date,
            customer_name='Other Customer',
            customer_email=self.other_user.email,
            customer_phone='+351911111111',
            fee_amount=decimal.Decimal('50.00'),
            discount_amount=decimal.Decimal('0.00'),
            total_amount=decimal.Decimal('50.00'),
            animal_price_amount=decimal.Decimal('1500.00'),
            reservation_deposit_percentage=decimal.Decimal('50.00'),
            reservation_deposit_amount=decimal.Decimal('750.00'),
        )
