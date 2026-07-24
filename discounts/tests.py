import decimal
from datetime import timedelta

from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from breeding.models import Animal
from discounts.forms import PromotionAdminForm
from discounts.models import Promotion
from discounts.services import PromotionUnavailable, quote_promotion
from reservations.models import Payment, PreReservation, Reservation
from reservations.exceptions import ReservationUnavailable
from reservations.services.reservation import (
    accept_pre_reservation,
    start_reservation_payment,
)
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class PromotionTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()

    def accepted_reservation(self):
        pre_reservation = self.reserve(self.dog)
        now = timezone.now()
        Payment.objects.filter(pre_reservation=pre_reservation).update(
            status=Payment.Status.PAID,
            paid_at=now,
        )
        PreReservation.objects.filter(pk=pre_reservation.pk).update(
            status=PreReservation.Status.AWAITING_REVIEW,
            confirmed_at=now,
        )
        return accept_pre_reservation(
            pre_reservation_id=pre_reservation.pk,
            admin_user=self.user,
        )

    def test_fixed_discount_is_capped_at_the_purchase_value(self):
        promotion = Promotion.objects.create(
            code='free-place',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('100.00'),
            scope=Promotion.Scope.ANY,
        )

        reservation = self.reserve(self.dog, promotion_code=promotion.code)

        self.assertEqual(reservation.fee_amount, decimal.Decimal('50.00'))
        self.assertEqual(reservation.discount_amount, decimal.Decimal('50.00'))
        self.assertEqual(reservation.total_amount, decimal.Decimal('0.00'))
        self.assertEqual(reservation.payment.status, Payment.Status.PAID)
        self.assertEqual(reservation.promotion_code, 'FREE-PLACE')

    def test_percentage_discount_is_rounded_to_currency_precision(self):
        Promotion.objects.create(
            code='quarter',
            discount_type=Promotion.DiscountType.PERCENTAGE,
            value=decimal.Decimal('25.00'),
            scope=Promotion.Scope.ANY,
        )

        quote = quote_promotion(
            code='quarter',
            target=self.dog,
            user=self.user,
            fee=decimal.Decimal('50.00'),
        )

        self.assertEqual(quote.discount_amount, decimal.Decimal('12.50'))
        self.assertEqual(quote.total_amount, decimal.Decimal('37.50'))

    def test_promotion_purchase_stage_is_enforced(self):
        pre_reservation_only = Promotion.objects.create(
            code='pre-only',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            purchase_stage=Promotion.PurchaseStage.PRE_RESERVATION,
        )
        reservation_only = Promotion.objects.create(
            code='reservation-only',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('10.00'),
            purchase_stage=Promotion.PurchaseStage.RESERVATION,
        )
        both = Promotion.objects.create(
            code='both-stages',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('3.00'),
            purchase_stage=Promotion.PurchaseStage.BOTH,
        )

        with self.assertRaisesMessage(
            PromotionUnavailable,
            'cannot be used for a reservation',
        ):
            quote_promotion(
                code=pre_reservation_only.code,
                target=self.dog,
                user=self.user,
                fee=decimal.Decimal('700.00'),
                purchase_stage=Promotion.PurchaseStage.RESERVATION,
            )
        with self.assertRaisesMessage(
            PromotionUnavailable,
            'cannot be used for a pre-reservation',
        ):
            quote_promotion(
                code=reservation_only.code,
                target=self.dog,
                user=self.user,
                fee=decimal.Decimal('50.00'),
            )

        for purchase_stage in (
            Promotion.PurchaseStage.PRE_RESERVATION,
            Promotion.PurchaseStage.RESERVATION,
        ):
            with self.subTest(purchase_stage=purchase_stage):
                quote = quote_promotion(
                    code=both.code,
                    target=self.dog,
                    user=self.user,
                    fee=decimal.Decimal('50.00'),
                    purchase_stage=purchase_stage,
                )
                self.assertEqual(
                    quote.discount_amount,
                    decimal.Decimal('3.00'),
                )

    def test_reservation_discount_is_snapshotted_and_capped(self):
        promotion = Promotion.objects.create(
            code='reservation-free',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('900.00'),
            purchase_stage=Promotion.PurchaseStage.RESERVATION,
        )
        reservation = self.accepted_reservation()

        reservation = start_reservation_payment(
            reservation_id=reservation.pk,
            user=self.user,
            accepted_terms=self.reservation_terms,
            promotion_code=promotion.code,
        )

        self.assertEqual(
            reservation.amount_before_discount,
            decimal.Decimal('700.00'),
        )
        self.assertEqual(
            reservation.discount_amount,
            decimal.Decimal('700.00'),
        )
        self.assertEqual(reservation.payment_amount, decimal.Decimal('0.00'))
        self.assertEqual(reservation.promotion, promotion)
        self.assertEqual(reservation.promotion_code, 'RESERVATION-FREE')
        self.assertEqual(reservation.status, Reservation.Status.CONFIRMED)
        self.assertEqual(reservation.payment.status, Payment.Status.PAID)

    def test_coupon_preview_does_not_create_a_pre_reservation(self):
        promotion = Promotion.objects.create(
            code='preview',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('10.00'),
        )
        self.client.force_login(self.user)
        data = self.checkout_data(promotion_code=promotion.code)
        data['action'] = 'apply_promotion'

        response = self.client.post(
            reverse('breeding:pre_reserve_dog', args=[self.dog.pk]),
            data,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Coupon PREVIEW applied')
        self.assertContains(response, '40.00 EUR')
        self.assertFalse(PreReservation.objects.exists())

    def test_coupon_preview_explains_invalid_and_inapplicable_codes(self):
        reservation_only = Promotion.objects.create(
            code='later',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('10.00'),
            purchase_stage=Promotion.PurchaseStage.RESERVATION,
        )
        self.client.force_login(self.user)
        url = reverse('breeding:pre_reserve_dog', args=[self.dog.pk])

        for code, expected_message in (
            ('missing', 'This promotion code is not valid.'),
            (
                reservation_only.code,
                'This promotion cannot be used for a pre-reservation.',
            ),
        ):
            with self.subTest(code=code):
                data = self.checkout_data(promotion_code=code)
                data['action'] = 'apply_promotion'
                response = self.client.post(url, data)

                self.assertContains(response, expected_message)
                self.assertFalse(PreReservation.objects.exists())

    def test_reservation_coupon_preview_updates_the_amount_without_payment(self):
        promotion = Promotion.objects.create(
            code='deposit-100',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('100.00'),
            purchase_stage=Promotion.PurchaseStage.RESERVATION,
        )
        reservation = self.accepted_reservation()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                'reservations:reservation_checkout',
                args=[reservation.public_id],
            ),
            {
                'terms': self.reservation_terms.pk,
                'promotion_code': promotion.code,
                'action': 'apply_promotion',
            },
        )

        self.assertContains(response, 'Coupon DEPOSIT-100 applied')
        self.assertContains(response, '600.00 EUR')
        self.assertFalse(
            Payment.objects.filter(animal_reservation=reservation).exists()
        )

    def test_breed_scoped_promotion_checks_selected_breeds(self):
        promotion = Promotion.objects.create(
            code='breed-only',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            scope=Promotion.Scope.BREEDS,
        )
        promotion.breeds.add(self.breed)

        quote = quote_promotion(
            code=promotion.code,
            target=self.dog,
            user=self.user,
            fee=decimal.Decimal('50.00'),
        )

        self.assertEqual(quote.promotion, promotion)

    def test_inactive_and_expired_promotions_are_rejected(self):
        promotion = Promotion.objects.create(
            code='expired',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            scope=Promotion.Scope.ANY,
            ends_at=timezone.now() - timedelta(seconds=1),
        )

        with self.assertRaises(PromotionUnavailable):
            quote_promotion(
                code=promotion.code,
                target=self.dog,
                user=self.user,
                fee=decimal.Decimal('50.00'),
            )

        promotion.ends_at = None
        promotion.active = False
        promotion.save(update_fields=['ends_at', 'active'])
        with self.assertRaises(PromotionUnavailable):
            quote_promotion(
                code=promotion.code,
                target=self.dog,
                user=self.user,
                fee=decimal.Decimal('50.00'),
            )

    def test_pending_reservation_consumes_promotion_limit(self):
        promotion = Promotion.objects.create(
            code='one-only',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            scope=Promotion.Scope.ANY,
            max_redemptions=1,
        )
        self.reserve(self.dog, promotion_code=promotion.code)
        second_dog = Animal.objects.create(
            breed=self.breed,
            name='Apollo',
            birth_date=timezone.localdate() - timedelta(days=300),
            gender='M',
            active=True,
            for_sale=True,
            price_in_euros=decimal.Decimal('1400.00'),
        )

        with self.assertRaisesMessage(ReservationUnavailable, 'reached its limit'):
            self.reserve(
                second_dog,
                user=self.other_user,
                promotion_code=promotion.code,
            )

    def test_admin_form_requires_a_target_for_selected_scope(self):
        form = PromotionAdminForm(
            data={
                'code': 'missing-breed',
                'discount_type': Promotion.DiscountType.FIXED,
                'purchase_stage': Promotion.PurchaseStage.PRE_RESERVATION,
                'value': '5.00',
                'scope': Promotion.Scope.BREEDS,
                'breeds': [],
                'dogs': [],
                'active': True,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('breeds', form.errors)

    def test_admin_form_clears_targets_that_do_not_match_scope(self):
        promotion = Promotion.objects.create(
            code='previously-targeted',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            scope=Promotion.Scope.BREEDS,
        )
        promotion.breeds.add(self.breed)
        promotion.dogs.add(self.dog)

        form = PromotionAdminForm(
            data={
                'code': 'all-dogs',
                'discount_type': Promotion.DiscountType.FIXED,
                'purchase_stage': Promotion.PurchaseStage.PRE_RESERVATION,
                'value': '5.00',
                'scope': Promotion.Scope.ANY,
                'breeds': [self.breed.pk],
                'dogs': [self.dog.pk],
                'active': True,
            },
            instance=promotion,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved_promotion = form.save()

        self.assertFalse(saved_promotion.breeds.exists())
        self.assertFalse(saved_promotion.dogs.exists())

    def test_used_promotion_cannot_be_deleted(self):
        promotion = Promotion.objects.create(
            code='used',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            scope=Promotion.Scope.ANY,
        )
        self.reserve(self.dog, promotion_code=promotion.code)

        with self.assertRaises(ProtectedError):
            promotion.delete()

    def test_promotion_used_for_a_reservation_cannot_be_deleted(self):
        promotion = Promotion.objects.create(
            code='used-on-reservation',
            discount_type=Promotion.DiscountType.FIXED,
            value=decimal.Decimal('5.00'),
            purchase_stage=Promotion.PurchaseStage.RESERVATION,
        )
        reservation = self.accepted_reservation()
        start_reservation_payment(
            reservation_id=reservation.pk,
            user=self.user,
            accepted_terms=self.reservation_terms,
            promotion_code=promotion.code,
        )

        with self.assertRaises(ProtectedError):
            promotion.delete()
