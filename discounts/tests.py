import decimal
from datetime import timedelta

from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from discounts.forms import PromotionAdminForm
from discounts.models import Promotion
from discounts.services import PromotionUnavailable, quote_promotion
from reservations.models import Payment
from reservations.exceptions import ReservationUnavailable
from reservations.tests.base import ReservationTestMixin


class PromotionTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()

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
        promotion = Promotion.objects.create(
            code='quarter',
            discount_type=Promotion.DiscountType.PERCENTAGE,
            value=decimal.Decimal('25.00'),
            scope=Promotion.Scope.DOGS,
        )

        quote = quote_promotion(
            code='quarter',
            target=self.dog,
            user=self.user,
            fee=decimal.Decimal('50.00'),
        )

        self.assertEqual(quote.discount_amount, decimal.Decimal('12.50'))

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
            target=self.litter,
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

        with self.assertRaisesMessage(ReservationUnavailable, 'reached its limit'):
            self.reserve(
                self.litter,
                user=self.other_user,
                promotion_code=promotion.code,
            )

    def test_admin_form_requires_a_target_for_selected_scope(self):
        form = PromotionAdminForm(
            data={
                'code': 'missing-breed',
                'discount_type': Promotion.DiscountType.FIXED,
                'value': '5.00',
                'scope': Promotion.Scope.BREEDS,
                'breeds': [],
                'dogs': [],
                'litters': [],
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
        promotion.litters.add(self.litter)

        form = PromotionAdminForm(
            data={
                'code': 'all-dogs',
                'discount_type': Promotion.DiscountType.FIXED,
                'value': '5.00',
                'scope': Promotion.Scope.DOGS,
                'breeds': [self.breed.pk],
                'dogs': [self.dog.pk],
                'litters': [self.litter.pk],
                'active': True,
            },
            instance=promotion,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved_promotion = form.save()

        self.assertFalse(saved_promotion.breeds.exists())
        self.assertFalse(saved_promotion.dogs.exists())
        self.assertFalse(saved_promotion.litters.exists())

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
