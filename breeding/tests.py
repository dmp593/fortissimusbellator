from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from breeding.services.litter_alerts import (
    get_or_create_alert_preference,
    process_birth_notification,
    set_litter_subscription,
)
from reservations.models import PreReservationTerms
from reservations.services.reservation import create_pending_reservation

from .social_media import wait_for_media_ready
from .models import (
    Animal,
    AnimalCertification,
    AnimalKind,
    Breed,
    Certification,
    Litter,
    LitterAlertPreference,
    LitterBirthAnnouncement,
    LitterBirthNotification,
)


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


class SocialMediaTests(TestCase):
    @override_settings(FACEBOOK_GRAPH_VERSION="v99.0")
    @patch("breeding.social_media.requests.get")
    def test_media_status_uses_configured_graph_version(self, get):
        response = get.return_value
        response.ok = True
        response.json.return_value = {"status_code": "FINISHED"}

        ready = wait_for_media_ready("creation-id", "access-token")

        self.assertTrue(ready)
        get.assert_called_once_with(
            "https://graph.facebook.com/v99.0/creation-id",
            params={
                "fields": "status_code",
                "access_token": "access-token",
            },
            timeout=15,
        )


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class BreedingPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        kind = AnimalKind.objects.create(name='Dog')
        cls.breed = Breed.objects.create(
            kind=kind,
            name='German Shepherd',
            cover='breeds/test.jpg',
        )
        today = timezone.localdate()
        cls.junior = Animal.objects.create(
            breed=cls.breed,
            name='Junior',
            birth_date=today - timedelta(days=270),
            gender='M',
            active=True,
            for_sale=True,
            for_breeding=True,
        )
        cls.adult = Animal.objects.create(
            breed=cls.breed,
            name='Adult',
            birth_date=today - timedelta(days=600),
            gender='F',
            active=True,
            for_sale=True,
        )
        cls.certification = Certification.objects.create(
            code='WB',
            name='Wesensbeurteilung',
            description='This detailed explanation belongs in chat knowledge.',
        )
        AnimalCertification.objects.create(
            animal=cls.junior,
            certification=cls.certification,
        )
        cls.litter = Litter.objects.create(
            breed=cls.breed,
            name='Expected Litter',
            status=Litter.LitterStatus.EXPECTING,
            expected_babies=5,
            active=True,
        )
        cls.user = get_user_model().objects.create_user(
            username='buyer',
            email='buyer@example.com',
            password='test-password',
        )

    def test_junior_filter_uses_six_to_twelve_month_range(self):
        response = self.client.get(
            reverse('breeding:buy_a_dog'),
            {'age': 'junior'},
        )

        self.assertEqual(list(response.context['dogs']), [self.junior])

    def test_public_list_page_size_is_capped(self):
        dog_response = self.client.get(
            reverse('breeding:buy_a_dog'),
            {'per_page': '100000'},
        )
        litter_response = self.client.get(
            reverse('breeding:upcoming_litters'),
            {'per_page': '100000'},
        )

        self.assertEqual(dog_response.context['dogs'].paginator.per_page, 48)
        self.assertEqual(
            litter_response.context['litters'].paginator.per_page,
            48,
        )

    def test_expecting_litter_offers_birth_alert_not_pre_reservation(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('breeding:litter_detail', args=[self.litter.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email me when they are born')
        self.assertNotContains(response, 'Pre-Reserve')
        self.assertNotContains(response, 'reserved /')

    def test_animal_pages_show_certification_codes_without_descriptions(self):
        urls = (
            reverse('breeding:buy_a_dog'),
            reverse('breeding:dog_detail', args=[self.junior.pk]),
            reverse('breeding:our_dogs'),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'WB')
                self.assertNotContains(response, self.certification.description)

    def test_missing_litter_redirects_to_namespaced_listing(self):
        response = self.client.get(
            reverse('breeding:litter_detail', args=[999999]),
        )

        self.assertRedirects(
            response,
            reverse('breeding:upcoming_litters'),
        )

    def test_dog_without_published_price_cannot_be_pre_reserved(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('breeding:pre_reserve_dog', args=[self.junior.pk]),
            follow=True,
        )

        self.assertRedirects(
            response,
            reverse('breeding:dog_detail', args=[self.junior.pk]),
        )
        self.assertContains(
            response,
            'Dogs without a published price cannot be pre-reserved.',
        )

    def test_pre_reserved_dog_has_no_contact_action_and_muted_image(self):
        self.junior.price_in_euros = Decimal('1500.00')
        self.junior.save(update_fields=['price_in_euros'])
        terms = PreReservationTerms.objects.current()
        create_pending_reservation(
            user=self.user,
            target_type='dog',
            target_id=self.junior.pk,
            checkout_data={
                'full_name': 'Buyer',
                'email': 'buyer@example.com',
                'phone': '+351912345678',
                'tax_number': '',
                'billing_address': '',
                'billing_postcode': '',
                'billing_city': '',
                'billing_country': 'PT',
                'promotion_code': '',
                'terms': terms,
                'accept_non_refundable': True,
            },
            language_code='en',
        )

        response = self.client.get(
            reverse('breeding:dog_detail', args=[self.junior.pk]),
        )

        self.assertContains(response, 'role="status"')
        self.assertContains(response, 'grayscale')
        self.assertContains(response, 'Pre-reserved')
        self.assertNotContains(
            response,
            '<span class="text-lg">Call Us</span>',
            html=True,
        )
        self.assertNotContains(
            response,
            reverse('breeding:pre_reserve_dog', args=[self.junior.pk]),
        )


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class LitterAnimalGenerationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        kind = AnimalKind.objects.create(name='Dog')
        cls.breed = Breed.objects.create(
            kind=kind,
            name='German Shepherd',
            cover='breeds/test.jpg',
        )
        cls.superuser = get_user_model().objects.create_superuser(
            username='litter-admin',
            email='litter-admin@example.com',
            password='test-password',
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def run_generation_action(self, litter):
        return self.client.post(
            reverse('admin:breeding_litter_changelist'),
            {
                'action': 'create_animals_from_litter',
                ACTION_CHECKBOX_NAME: [str(litter.pk)],
            },
        )

    def test_action_creates_only_remaining_actual_babies(self):
        litter = Litter.objects.create(
            breed=self.breed,
            name='Born Litter',
            birth_date=timezone.localdate(),
            babies=5,
            status=Litter.LitterStatus.BORN,
            offspring_pre_reservation_enabled=True,
            offspring_pre_reservation_fee=Decimal('75.00'),
            offspring_reservation_deposit_percentage=Decimal('40.00'),
        )
        for index in range(2):
            Animal.objects.create(
                breed=self.breed,
                litter=litter,
                name=f'Existing {index}',
                birth_date=litter.birth_date,
                gender='?',
            )

        response = self.run_generation_action(litter)

        self.assertEqual(response.status_code, 302)
        generated = list(litter.animals.order_by('pk'))[2:]
        self.assertEqual(len(generated), 3)
        self.assertTrue(all(animal.for_sale for animal in generated))
        self.assertTrue(
            all(animal.pre_reservation_enabled for animal in generated),
        )
        self.assertTrue(
            all(
                animal.pre_reservation_fee == Decimal('75.00')
                for animal in generated
            ),
        )
        self.assertTrue(
            all(
                animal.reservation_deposit_percentage == Decimal('40.00')
                for animal in generated
            ),
        )

        self.run_generation_action(litter)
        self.assertEqual(litter.animals.count(), 5)

    def test_action_copies_disabled_pre_reservation_configuration(self):
        litter = Litter.objects.create(
            breed=self.breed,
            name='Private Litter',
            birth_date=timezone.localdate(),
            babies=2,
            status=Litter.LitterStatus.BORN,
            offspring_pre_reservation_enabled=False,
            offspring_pre_reservation_fee=Decimal('90.00'),
            offspring_reservation_deposit_percentage=Decimal('60.00'),
        )

        self.run_generation_action(litter)

        generated = list(litter.animals.order_by('pk'))
        self.assertEqual(len(generated), 2)
        self.assertTrue(
            all(not animal.pre_reservation_enabled for animal in generated),
        )
        self.assertTrue(
            all(
                animal.pre_reservation_fee == Decimal('90.00')
                for animal in generated
            ),
        )
        self.assertTrue(
            all(
                animal.reservation_deposit_percentage == Decimal('60.00')
                for animal in generated
            ),
        )

    def test_action_requires_actual_birth_date(self):
        litter = Litter.objects.create(
            breed=self.breed,
            name='Incomplete Litter',
            babies=2,
            status=Litter.LitterStatus.BORN,
        )

        response = self.run_generation_action(litter)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(litter.animals.exists())


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class ParentGenderAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        kind = AnimalKind.objects.create(name='Dog')
        breed = Breed.objects.create(
            kind=kind,
            name='German Shepherd',
            cover='breeds/test.jpg',
        )
        today = timezone.localdate()
        cls.male = Animal.objects.create(
            breed=breed,
            name='Male parent',
            birth_date=today,
            gender='M',
        )
        cls.female = Animal.objects.create(
            breed=breed,
            name='Female parent',
            birth_date=today,
            gender='F',
        )
        cls.unknown = Animal.objects.create(
            breed=breed,
            name='Unknown parent',
            birth_date=today,
            gender='?',
        )
        cls.superuser = get_user_model().objects.create_superuser(
            username='parent-admin',
            email='parent-admin@example.com',
            password='test-password',
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_parent_autocomplete_filters_gender_for_animals_and_litters(self):
        cases = (
            ('animal', 'father', self.male),
            ('animal', 'mother', self.female),
            ('litter', 'father', self.male),
            ('litter', 'mother', self.female),
        )

        for model_name, field_name, expected_parent in cases:
            with self.subTest(
                model_name=model_name,
                field_name=field_name,
            ):
                response = self.client.get(
                    reverse('admin:autocomplete'),
                    {
                        'app_label': 'breeding',
                        'model_name': model_name,
                        'field_name': field_name,
                        'term': 'parent',
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    {result['id'] for result in response.json()['results']},
                    {str(expected_parent.pk)},
                )


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='noreply@example.com',
    LITTER_ALERT_MAX_AUTOMATIC_ATTEMPTS=3,
)
class LitterBirthAlertTests(TestCase):
    def setUp(self):
        kind = AnimalKind.objects.create(name='Dog')
        self.breed = Breed.objects.create(
            kind=kind,
            name='German Shepherd',
            cover='breeds/test.jpg',
        )
        self.other_breed = Breed.objects.create(
            kind=kind,
            name='Malinois',
            cover='breeds/test-2.jpg',
        )
        self.user = get_user_model().objects.create_user(
            username='alerts',
            email='alerts@example.com',
        )
        self.litter = Litter.objects.create(
            breed=self.breed,
            name='Future Litter',
            status=Litter.LitterStatus.EXPECTING,
            active=True,
        )

    def announce_birth(self):
        self.litter.status = Litter.LitterStatus.BORN
        self.litter.birth_date = timezone.localdate()
        self.litter.babies = 4
        self.litter.save(
            update_fields=['status', 'birth_date', 'babies'],
        )
        return LitterBirthAnnouncement.objects.get(litter=self.litter)

    def test_individual_subscription_queues_and_sends_durable_notification(self):
        set_litter_subscription(
            user=self.user,
            litter=self.litter,
            enabled=True,
            language_code='en',
        )

        announcement = self.announce_birth()
        notification = announcement.notifications.get(user=self.user)
        notification = process_birth_notification(notification.pk)

        self.assertEqual(notification.status, LitterBirthNotification.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('4', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')
        self.assertIn('Fortissimus Bellator', mail.outbox[0].alternatives[0][0])
        self.assertIn(
            '/en/upcoming-litters/',
            mail.outbox[0].alternatives[0][0],
        )
        self.assertIn(
            '/en/profile/litter-alerts/',
            mail.outbox[0].alternatives[0][0],
        )

    def test_explicit_unsubscribe_overrides_all_breeds(self):
        preference = get_or_create_alert_preference(self.user)
        preference.scope = LitterAlertPreference.Scope.ALL
        preference.save(update_fields=['scope'])
        set_litter_subscription(
            user=self.user,
            litter=self.litter,
            enabled=False,
            language_code='en',
        )

        announcement = self.announce_birth()

        self.assertFalse(
            announcement.notifications.filter(user=self.user).exists(),
        )

    def test_selected_breed_general_preference(self):
        preference = get_or_create_alert_preference(self.user)
        preference.scope = LitterAlertPreference.Scope.SELECTED_BREEDS
        preference.save(update_fields=['scope'])
        preference.breeds.add(self.breed)

        announcement = self.announce_birth()

        self.assertTrue(
            announcement.notifications.filter(user=self.user).exists(),
        )

    def test_unsubscribe_cancels_queued_delivery(self):
        set_litter_subscription(
            user=self.user,
            litter=self.litter,
            enabled=True,
            language_code='en',
        )
        announcement = self.announce_birth()

        set_litter_subscription(
            user=self.user,
            litter=self.litter,
            enabled=False,
            language_code='en',
        )

        notification = announcement.notifications.get(user=self.user)
        self.assertEqual(
            notification.status,
            LitterBirthNotification.Status.CANCELLED,
        )

    def test_email_failure_is_retryable_without_duplicate_notification(self):
        set_litter_subscription(
            user=self.user,
            litter=self.litter,
            enabled=True,
            language_code='en',
        )
        notification = self.announce_birth().notifications.get(user=self.user)

        with (
            patch(
                'breeding.services.litter_alerts.send_branded_email',
                side_effect=RuntimeError('mail outage'),
            ),
            patch('breeding.services.litter_alerts.logger.exception'),
        ):
            failed = process_birth_notification(notification.pk)
        self.assertEqual(failed.status, LitterBirthNotification.Status.FAILED)
        self.assertIsNotNone(failed.next_retry_at)

        with patch('breeding.services.litter_alerts.send_branded_email'):
            sent = process_birth_notification(notification.pk)
        self.assertEqual(sent.status, LitterBirthNotification.Status.SENT)
        self.assertEqual(
            LitterBirthNotification.objects.filter(
                announcement=notification.announcement,
                user=self.user,
            ).count(),
            1,
        )
