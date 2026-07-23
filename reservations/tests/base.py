from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from breeding.models import Animal, AnimalKind, Breed, Litter
from reservations.models import PreReservation, PreReservationTerms
from reservations.services.reservation import create_pending_reservation


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


class ReservationTestMixin:
    def create_domain_data(self):
        self.terms, _ = PreReservationTerms.objects.update_or_create(
            version='pre-reservation-v1',
            defaults={
                'description': (
                    'The pre-reservation fee is non-refundable if the customer '
                    'cancels.'
                ),
                'published_at': timezone.now(),
            },
        )
        self.kind = AnimalKind.objects.create(name='Dog')
        self.breed = Breed.objects.create(
            kind=self.kind,
            name='German Shepherd',
            cover='breeds/test.jpg',
        )
        self.user = get_user_model().objects.create_user(
            username='customer',
            email='customer@example.com',
            password='test-password',
        )
        self.other_user = get_user_model().objects.create_user(
            username='other-customer',
            email='other@example.com',
            password='test-password',
        )
        self.dog = Animal.objects.create(
            breed=self.breed,
            name='Athena',
            birth_date=timezone.localdate() - timedelta(days=300),
            gender='F',
            active=True,
            for_sale=True,
        )
        self.litter = Litter.objects.create(
            breed=self.breed,
            name='A Litter',
            birth_date=timezone.localdate(),
            babies=5,
            status=Litter.LitterStatus.BORN,
            active=True,
            pre_reservation_capacity=3,
        )

    def checkout_data(self, *, promotion_code=''):
        return {
            'full_name': 'Customer Example',
            'email': 'customer@example.com',
            'phone': '+351900000000',
            'tax_number': '999999990',
            'billing_address': 'Example Street 1',
            'billing_postcode': '1000-001',
            'billing_city': 'Lisbon',
            'billing_country': 'PT',
            'promotion_code': promotion_code,
            'terms': self.terms.pk,
            'accept_non_refundable': True,
        }

    def reserve(self, target, *, user=None, promotion_code=''):
        target_type = (
            PreReservation.TargetType.DOG
            if isinstance(target, Animal)
            else PreReservation.TargetType.LITTER
        )
        checkout_data = self.checkout_data(promotion_code=promotion_code)
        checkout_data['terms'] = self.terms
        return create_pending_reservation(
            user=user or self.user,
            target_type=target_type,
            target_id=target.pk,
            checkout_data=checkout_data,
            language_code='en',
        )
