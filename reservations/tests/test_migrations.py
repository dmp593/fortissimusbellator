import datetime

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


class CrossDatabaseUniqueGuardsMigrationTests(TransactionTestCase):
    migrate_from = ('reservations', '0015_payment_checkout_started_at')
    migrate_to = ('reservations', '0016_cross_database_unique_guards')

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_migration)
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.old_apps = executor.loader.project_state(
            [self.migrate_from]
        ).apps

    def _restore_latest_migration(self):
        MigrationExecutor(connection).migrate(
            [
                ('reservations', '0017_canonical_animal_sales'),
                ('breeding', '0017_remove_duplicated_sale_fields'),
            ]
        )

    def test_migration_succeeds_with_existing_sale_case(self):
        case_id, animal_id = self._create_sale_case(self.old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])

        migrated_apps = executor.loader.project_state([self.migrate_to]).apps
        SaleCase = migrated_apps.get_model(
            'reservations',
            'AnimalSaleCase',
        )
        sale_case = SaleCase.objects.get(pk=case_id)
        self.assertEqual(sale_case.active_animal_key, animal_id)
        self.assert_duplicate_active_case_is_rejected(
            SaleCase,
            sale_case,
        )

        repeated_executor = MigrationExecutor(connection)
        self.assertEqual(
            repeated_executor.migration_plan([self.migrate_to]),
            [],
        )
        repeated_executor.migrate([self.migrate_to])
        self.assertEqual(
            MigrationRecorder(connection)
            .migration_qs.filter(
                app=self.migrate_to[0],
                name=self.migrate_to[1],
            )
            .count(),
            1,
        )

    def _create_sale_case(self, apps):
        user_app, user_model = settings.AUTH_USER_MODEL.split('.')
        User = apps.get_model(user_app, user_model)
        AnimalKind = apps.get_model('breeding', 'AnimalKind')
        Breed = apps.get_model('breeding', 'Breed')
        Animal = apps.get_model('breeding', 'Animal')
        SaleCase = apps.get_model('reservations', 'AnimalSaleCase')

        user = User.objects.create(
            username='migration-customer',
            email='migration@example.com',
        )
        kind = AnimalKind.objects.create(
            name='Dog',
        )
        breed = Breed.objects.create(
            kind=kind,
            name='Migration Breed',
            cover='breeds/migration.jpg',
            description='Migration test breed',
        )
        animal = Animal.objects.create(
            breed=breed,
            name='Migration Dog',
            description='Migration test dog',
            birth_date=datetime.date(2025, 1, 1),
            hair_type='short',
        )
        sale_case = SaleCase.objects.create(
            user=user,
            animal=animal,
            status='pre_reservation',
            target_name=animal.name,
            target_breed=breed.name,
            customer_name='Migration Customer',
            customer_email=user.email,
        )
        return sale_case.pk, animal.pk

    def assert_duplicate_active_case_is_rejected(
        self,
        SaleCase,
        sale_case,
    ):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SaleCase.objects.create(
                user=sale_case.user,
                animal=sale_case.animal,
                status='pre_reservation',
                target_name=sale_case.target_name,
                target_breed=sale_case.target_breed,
                customer_name=sale_case.customer_name,
                customer_email=sale_case.customer_email,
            )


class CanonicalAnimalSalesMigrationTests(TransactionTestCase):
    migrate_from = [
        ('reservations', '0016_cross_database_unique_guards'),
        ('breeding', '0016_animal_reservation_offer_hours'),
    ]
    migrate_to = [
        ('reservations', '0017_canonical_animal_sales'),
        ('breeding', '0017_remove_duplicated_sale_fields'),
    ]

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_migration)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.old_apps = executor.loader.project_state(self.migrate_from).apps

    def _restore_latest_migration(self):
        MigrationExecutor(connection).migrate(self.migrate_to)

    def test_twenty_one_legacy_sold_dogs_keep_their_sold_state(self):
        animal_ids, sold_date = self._create_legacy_sold_animals(
            self.old_apps,
            count=21,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        Animal = migrated_apps.get_model('breeding', 'Animal')
        AnimalSale = migrated_apps.get_model(
            'reservations',
            'AnimalSale',
        )
        AnimalSaleCase = migrated_apps.get_model(
            'reservations',
            'AnimalSaleCase',
        )

        with self.assertRaises(FieldDoesNotExist):
            Animal._meta.get_field('sold_at')
        with self.assertRaises(FieldDoesNotExist):
            Animal._meta.get_field('sold_to')

        sales = AnimalSale.objects.filter(
            sale_case__animal_id__in=animal_ids,
        )
        self.assertEqual(sales.count(), 21)
        self.assertEqual(
            sales.filter(
                source='legacy',
                sold_at=sold_date,
                final_price__isnull=True,
                charge__isnull=True,
                voided_at__isnull=True,
            ).count(),
            21,
        )
        self.assertEqual(
            AnimalSaleCase.objects.filter(
                animal_id__in=animal_ids,
                origin='legacy',
                status='sold',
                user__isnull=True,
            ).count(),
            21,
        )

        repeated_executor = MigrationExecutor(connection)
        self.assertEqual(
            repeated_executor.migration_plan(self.migrate_to),
            [],
        )
        repeated_executor.migrate(self.migrate_to)

    def test_duplicate_inferred_sold_cases_are_reconciled(self):
        animal_ids, _ = self._create_legacy_sold_animals(
            self.old_apps,
            count=1,
        )
        SaleCase = self.old_apps.get_model(
            'reservations',
            'AnimalSaleCase',
        )
        animal_id = animal_ids[0]
        for index in range(2):
            SaleCase.objects.create(
                animal_id=animal_id,
                status='sold',
                target_name='Legacy Sold Dog 1',
                target_breed='Legacy Breed',
                customer_name=f'Historical customer {index + 1}',
                customer_email=f'history-{index + 1}@example.com',
            )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        AnimalSale = migrated_apps.get_model(
            'reservations',
            'AnimalSale',
        )
        SaleCase = migrated_apps.get_model(
            'reservations',
            'AnimalSaleCase',
        )

        self.assertEqual(
            AnimalSale.objects.filter(
                sale_case__animal_id=animal_id,
                source='legacy',
            ).count(),
            1,
        )
        self.assertEqual(
            SaleCase.objects.filter(
                animal_id=animal_id,
                status='sold',
            ).count(),
            1,
        )
        self.assertEqual(
            SaleCase.objects.filter(
                animal_id=animal_id,
                status='closed',
            ).count(),
            1,
        )

    def _create_legacy_sold_animals(self, apps, *, count):
        AnimalKind = apps.get_model('breeding', 'AnimalKind')
        Breed = apps.get_model('breeding', 'Breed')
        Animal = apps.get_model('breeding', 'Animal')
        kind = AnimalKind.objects.create(name='Legacy Dog')
        breed = Breed.objects.create(
            kind=kind,
            name='Legacy Breed',
            cover='breeds/legacy.jpg',
            description='Legacy migration test breed',
        )
        sold_date = datetime.date(2024, 5, 20)
        animal_ids = []
        for index in range(count):
            animal = Animal.objects.create(
                breed=breed,
                name=f'Legacy Sold Dog {index + 1}',
                description='Legacy sold dog',
                birth_date=datetime.date(2023, 1, 1),
                hair_type='short',
                price_in_euros='1500.00',
                sold_at=sold_date,
                sold_to_id=None,
                active=True,
                for_sale=True,
            )
            animal_ids.append(animal.pk)
        return animal_ids, sold_date
