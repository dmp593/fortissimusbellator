from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Animal, AnimalKind, Breed, Litter


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
        )
        cls.adult = Animal.objects.create(
            breed=cls.breed,
            name="Adult",
            birth_date=today - timedelta(days=600),
            gender="F",
            active=True,
            for_sale=True,
        )
        cls.litter = Litter.objects.create(
            breed=cls.breed,
            name="Current Litter",
            status=Litter.LitterStatus.BORN,
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

    def test_dog_detail_uses_existing_javascript_asset(self):
        response = self.client.get(
            reverse("breeding:dog_detail", args=[self.junior.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/pages/dog_detail/index.js")

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

        self.assertRedirects(response, reverse("breeding:buy_a_dog"))
