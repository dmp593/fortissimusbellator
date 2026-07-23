from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reservations.models import PreReservationTerms
from reservations.services.reservation import create_pending_reservation

from .models import (
    Animal,
    AnimalCertification,
    AnimalKind,
    Breed,
    Certification,
    Litter,
)


TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class BreedingPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        kind = AnimalKind.objects.create(name="Dog")
        cls.breed = Breed.objects.create(
            kind=kind,
            name="German Shepherd",
            cover="breeds/test.jpg",
        )
        today = timezone.localdate()
        cls.junior = Animal.objects.create(
            breed=cls.breed,
            name="Junior",
            birth_date=today - timedelta(days=270),
            gender="M",
            active=True,
            for_sale=True,
            for_breeding=True,
        )
        cls.adult = Animal.objects.create(
            breed=cls.breed,
            name="Adult",
            birth_date=today - timedelta(days=600),
            gender="F",
            active=True,
            for_sale=True,
        )
        cls.certification = Certification.objects.create(
            code="WB",
            name="Wesensbeurteilung",
            description="This detailed explanation belongs in chat knowledge.",
        )
        AnimalCertification.objects.create(
            animal=cls.junior,
            certification=cls.certification,
        )
        cls.litter = Litter.objects.create(
            breed=cls.breed,
            name="Current Litter",
            status=Litter.LitterStatus.BORN,
            babies=5,
            pre_reservation_capacity=3,
            active=True,
        )
        cls.user = get_user_model().objects.create_user(
            username="buyer",
            password="test-password",
        )

    def test_junior_filter_uses_six_to_twelve_month_range(self):
        response = self.client.get(
            reverse("breeding:buy_a_dog"),
            {"age": "junior"},
        )

        self.assertEqual(list(response.context["dogs"]), [self.junior])

    def test_litter_pre_reserve_route_and_shared_component_render(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("breeding:pre_reserve_litter", args=[self.litter.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current Litter")
        self.assertContains(response, 'href="tel:+351924454382"')
        self.assertContains(response, "ui-card")

    def test_animal_pages_show_certification_codes_without_descriptions(self):
        urls = (
            reverse("breeding:buy_a_dog"),
            reverse("breeding:dog_detail", args=[self.junior.pk]),
            reverse("breeding:our_dogs"),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "WB")
                self.assertNotContains(
                    response,
                    self.certification.description,
                )

    def test_missing_detail_redirects_to_namespaced_listing(self):
        response = self.client.get(
            reverse("breeding:litter_detail", args=[999999])
        )

        self.assertRedirects(
            response,
            reverse("breeding:upcoming_litters"),
        )

    def test_sold_dog_cannot_be_pre_reserved(self):
        self.junior.sold_at = timezone.localdate()
        self.junior.save(update_fields=["sold_at"])
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("breeding:pre_reserve_dog", args=[self.junior.pk])
        )

        self.assertRedirects(
            response,
            reverse("breeding:dog_detail", args=[self.junior.pk]),
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
        cls.terms = PreReservationTerms.objects.current()
        cls.superuser = get_user_model().objects.create_superuser(
            username='litter-admin',
            email='litter-admin@example.com',
            password='test-password',
        )
        cls.customers = [
            get_user_model().objects.create_user(
                username=f'litter-customer-{index}',
                email=f'litter-customer-{index}@example.com',
                password='test-password',
            )
            for index in range(2)
        ]

    def setUp(self):
        self.client.force_login(self.superuser)

    def _reserve_litter_place(self, litter, user):
        return create_pending_reservation(
            user=user,
            target_type='litter',
            target_id=litter.pk,
            checkout_data={
                'full_name': user.get_username(),
                'email': user.email,
                'phone': '+351900000000',
                'tax_number': '',
                'billing_address': '',
                'billing_postcode': '',
                'billing_city': '',
                'billing_country': 'PT',
                'promotion_code': '',
                'terms': self.terms,
                'accept_non_refundable': True,
            },
            language_code='en',
        )

    def _run_generation_action(self, litter):
        return self.client.post(
            reverse('admin:breeding_litter_changelist'),
            {
                'action': 'create_animals_from_litter',
                ACTION_CHECKBOX_NAME: [str(litter.pk)],
            },
        )

    def test_action_creates_only_babies_without_litter_reservations(self):
        litter = Litter.objects.create(
            breed=self.breed,
            name='Reserved Litter',
            birth_date=timezone.localdate(),
            babies=5,
            status=Litter.LitterStatus.BORN,
            pre_reservation_capacity=3,
            pre_reservation_enabled=True,
            pre_reservation_fee=Decimal('75.00'),
        )
        for customer in self.customers:
            self._reserve_litter_place(litter, customer)

        response = self._run_generation_action(litter)

        self.assertEqual(response.status_code, 302)
        generated_animals = list(litter.animals.order_by('pk'))
        self.assertEqual(len(generated_animals), 3)
        self.assertTrue(all(animal.for_sale for animal in generated_animals))
        self.assertTrue(
            all(animal.pre_reservation_enabled for animal in generated_animals)
        )
        self.assertTrue(
            all(
                animal.pre_reservation_fee == Decimal('75.00')
                for animal in generated_animals
            )
        )
        litter.refresh_from_db()
        self.assertEqual(litter.pre_reservation_capacity, 2)

        self._run_generation_action(litter)
        self.assertEqual(litter.animals.count(), 3)

    def test_action_copies_disabled_pre_reservation_configuration(self):
        litter = Litter.objects.create(
            breed=self.breed,
            name='Private Litter',
            birth_date=timezone.localdate(),
            babies=2,
            status=Litter.LitterStatus.BORN,
            pre_reservation_capacity=0,
            pre_reservation_enabled=False,
            pre_reservation_fee=Decimal('90.00'),
        )

        response = self._run_generation_action(litter)

        self.assertEqual(response.status_code, 302)
        generated_animals = list(litter.animals.order_by('pk'))
        self.assertEqual(len(generated_animals), 2)
        self.assertTrue(
            all(
                not animal.pre_reservation_enabled
                for animal in generated_animals
            )
        )
        self.assertTrue(
            all(
                animal.pre_reservation_fee == Decimal('90.00')
                for animal in generated_animals
            )
        )
