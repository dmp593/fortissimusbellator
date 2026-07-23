from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reservations.exceptions import ReservationUnavailable
from reservations.models import PreReservation, PreReservationTerms
from reservations.tests.base import ReservationTestMixin, TEST_STORAGES


class PreReservationTermsFixtureTests(TestCase):
    fixtures = ('pre_reservation_terms_v1',)

    def test_fixture_installs_the_published_v1_terms(self):
        terms = PreReservationTerms.objects.current()

        self.assertIsNotNone(terms)
        self.assertEqual(terms.version, 'pre-reservation-v1')
        self.assertIn('non-refundable', terms.description_en)
        self.assertIn(
            'deducted in full from the final price of the dog',
            terms.description_en,
        )
        self.assertIn('não é reembolsável', terms.description_pt)
        self.assertIn(
            'deduzido na totalidade do preço final do cão',
            terms.description_pt,
        )


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class PreReservationTermsTests(ReservationTestMixin, TestCase):
    def setUp(self):
        self.create_domain_data()
        self.superuser = get_user_model().objects.create_superuser(
            username='terms-admin',
            email='terms-admin@example.com',
            password='test-password',
        )

    def test_latest_published_terms_are_current_and_public(self):
        self.terms.published_at = timezone.now() - timedelta(days=1)
        self.terms.save(update_fields=['published_at'])
        current = PreReservationTerms.objects.create(
            version='pre-reservation-v2',
            description='Current non-refundable terms.',
            published_at=timezone.now(),
        )
        PreReservationTerms.objects.create(
            version='pre-reservation-v3',
            description='Future terms.',
            published_at=timezone.now() + timedelta(days=1),
        )
        PreReservationTerms.objects.create(
            version='draft',
            description='Draft terms.',
        )

        self.assertEqual(PreReservationTerms.objects.current(), current)
        response = self.client.get(reverse('pre_reservation_terms'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Current non-refundable terms.')
        self.assertNotContains(response, 'Future terms.')

    def test_checkout_renders_terms_and_records_exact_version(self):
        self.client.force_login(self.user)
        checkout_url = reverse('breeding:pre_reserve_dog', args=[self.dog.pk])

        response = self.client.get(checkout_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.terms.description)
        self.assertContains(response, f'value="{self.terms.pk}"')

        reservation = self.reserve(self.dog)
        self.assertEqual(reservation.terms, self.terms)
        self.assertIsNotNone(reservation.non_refundable_accepted_at)

    def test_customer_must_review_terms_published_after_form_was_opened(self):
        self.client.force_login(self.user)
        newer_terms = PreReservationTerms.objects.create(
            version='pre-reservation-v2',
            description='Updated terms requiring renewed consent.',
            published_at=timezone.now() + timedelta(seconds=1),
        )
        PreReservationTerms.objects.filter(pk=newer_terms.pk).update(
            published_at=timezone.now()
        )
        checkout_data = self.checkout_data()
        checkout_data['terms'] = self.terms.pk

        response = self.client.post(
            reverse('breeding:pre_reserve_dog', args=[self.dog.pk]),
            data=checkout_data,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'terms were updated')
        self.assertFalse(PreReservation.objects.exists())

    def test_service_rejects_a_terms_version_that_is_no_longer_current(self):
        self.terms.published_at = timezone.now() - timedelta(seconds=1)
        self.terms.save(update_fields=['published_at'])
        PreReservationTerms.objects.create(
            version='pre-reservation-v2',
            description='New current terms.',
            published_at=timezone.now(),
        )

        with self.assertRaisesMessage(ReservationUnavailable, 'terms were updated'):
            self.reserve(self.dog)

    def test_checkout_is_unavailable_without_published_terms(self):
        PreReservationTerms.objects.all().delete()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('breeding:pre_reserve_dog', args=[self.dog.pk])
        )

        self.assertRedirects(
            response,
            reverse('breeding:dog_detail', args=[self.dog.pk]),
        )
        self.assertFalse(PreReservation.objects.exists())

    def test_admin_cannot_change_or_delete_used_terms(self):
        self.reserve(self.dog)
        self.client.force_login(self.superuser)
        change_url = reverse(
            'admin:reservations_prereservationterms_change',
            args=[self.terms.pk],
        )
        delete_url = reverse(
            'admin:reservations_prereservationterms_delete',
            args=[self.terms.pk],
        )

        self.assertEqual(self.client.get(change_url).status_code, 200)
        self.assertEqual(
            self.client.post(
                change_url,
                {
                    'version': 'changed',
                    'description_en': 'Changed terms.',
                },
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get(delete_url).status_code, 403)
        self.terms.refresh_from_db()
        self.assertEqual(self.terms.version, 'pre-reservation-v1')

    def test_admin_can_delete_unused_terms(self):
        unused = PreReservationTerms.objects.create(
            version='unused-draft',
            description='Unused draft.',
        )
        self.client.force_login(self.superuser)
        delete_url = reverse(
            'admin:reservations_prereservationterms_delete',
            args=[unused.pk],
        )

        self.assertEqual(self.client.get(delete_url).status_code, 200)
        response = self.client.post(delete_url, {'post': 'yes'})

        self.assertRedirects(
            response,
            reverse('admin:reservations_prereservationterms_changelist'),
        )
        self.assertFalse(PreReservationTerms.objects.filter(pk=unused.pk).exists())
