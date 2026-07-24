import decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def migrate_legacy_animal_sales(apps, schema_editor):
    Animal = apps.get_model('breeding', 'Animal')
    AnimalSale = apps.get_model('reservations', 'AnimalSale')
    AnimalSaleCase = apps.get_model('reservations', 'AnimalSaleCase')

    for animal in (
        Animal.objects.filter(sold_at__isnull=False)
        .select_related('breed', 'sold_to')
        .order_by('pk')
        .iterator()
    ):
        existing_sale = (
            AnimalSale.objects.filter(
                sale_case__animal_id=animal.pk,
                voided_at__isnull=True,
            )
            .select_related('sale_case')
            .order_by('-created_at', '-pk')
            .first()
        )
        if existing_sale is not None:
            canonical_case = existing_sale.sale_case
            if canonical_case.status != 'sold':
                canonical_case.status = 'sold'
                canonical_case.closed_at = None
                canonical_case.save(
                    update_fields=['status', 'closed_at', 'updated_at'],
                )
        else:
            canonical_case = (
                AnimalSaleCase.objects.filter(
                    animal_id=animal.pk,
                    status__in=('sold', 'reservation', 'pre_reservation'),
                )
                .order_by(
                    models.Case(
                        models.When(status='sold', then=models.Value(0)),
                        models.When(status='reservation', then=models.Value(1)),
                        default=models.Value(2),
                    ),
                    '-created_at',
                    '-pk',
                )
                .first()
            )
            if canonical_case is None:
                customer = animal.sold_to
                customer_name = ''
                customer_email = ''
                if customer is not None:
                    customer_name = (
                        customer.get_full_name()
                        or customer.get_username()
                    )
                    customer_email = customer.email
                price = animal.price_in_euros
                if price is not None and animal.discount_in_euros:
                    price -= animal.discount_in_euros
                deposit_percentage = (
                    animal.reservation_deposit_percentage
                )
                deposit_amount = (
                    price
                    * deposit_percentage
                    / decimal.Decimal('100')
                    if price is not None and deposit_percentage is not None
                    else None
                )
                canonical_case = AnimalSaleCase.objects.create(
                    user_id=animal.sold_to_id,
                    animal_id=animal.pk,
                    origin='legacy',
                    status='sold',
                    target_name=animal.name,
                    target_breed=animal.breed.name,
                    target_birth_date=animal.birth_date,
                    animal_price_amount=price,
                    reservation_deposit_percentage=deposit_percentage,
                    reservation_deposit_amount=deposit_amount,
                    customer_name=customer_name,
                    customer_email=customer_email,
                    currency='EUR',
                )
            else:
                canonical_case.status = 'sold'
                if canonical_case.user_id is None and animal.sold_to_id:
                    canonical_case.user_id = animal.sold_to_id
                canonical_case.closed_at = None
                canonical_case.save(
                    update_fields=['status', 'user', 'closed_at', 'updated_at'],
                )

            AnimalSale.objects.create(
                source='legacy',
                sale_case_id=canonical_case.pk,
                charge_id=None,
                final_price=None,
                sold_at=animal.sold_at,
                notes=(
                    'Imported from legacy Animal.sold_at. The original final '
                    'price and payment method were not recorded.'
                ),
            )

        (
            AnimalSaleCase.objects.filter(
                animal_id=animal.pk,
                status__in=('pre_reservation', 'reservation', 'sold'),
            )
            .exclude(pk=canonical_case.pk)
            .update(
                status='closed',
                closed_at=timezone.now(),
                updated_at=timezone.now(),
            )
        )

    AnimalSaleCase.objects.filter(
        status='sold',
        sale__isnull=True,
    ).update(
        status='closed',
        closed_at=timezone.now(),
        updated_at=timezone.now(),
    )

    duplicated_blocking_animal_ids = (
        AnimalSaleCase.objects.filter(
            animal_id__isnull=False,
            status__in=('pre_reservation', 'reservation', 'sold'),
        )
        .values('animal_id')
        .annotate(total=models.Count('pk'))
        .filter(total__gt=1)
        .values_list('animal_id', flat=True)
    )
    for animal_id in duplicated_blocking_animal_ids.iterator():
        canonical_case = (
            AnimalSaleCase.objects.filter(
                animal_id=animal_id,
                status__in=('pre_reservation', 'reservation', 'sold'),
                sale__voided_at__isnull=True,
            )
            .order_by('-sale__created_at', '-created_at', '-pk')
            .first()
        )
        if canonical_case is None:
            canonical_case = (
                AnimalSaleCase.objects.filter(
                    animal_id=animal_id,
                    status__in=('pre_reservation', 'reservation', 'sold'),
                )
                .order_by('-created_at', '-pk')
                .first()
            )
        (
            AnimalSaleCase.objects.filter(
                animal_id=animal_id,
                status__in=('pre_reservation', 'reservation', 'sold'),
            )
            .exclude(pk=canonical_case.pk)
            .update(
                status='closed',
                closed_at=timezone.now(),
                updated_at=timezone.now(),
            )
        )


def restore_legacy_animal_fields(apps, schema_editor):
    Animal = apps.get_model('breeding', 'Animal')
    AnimalSale = apps.get_model('reservations', 'AnimalSale')
    AnimalSaleCase = apps.get_model('reservations', 'AnimalSaleCase')

    for sale in (
        AnimalSale.objects.filter(
            voided_at__isnull=True,
            sale_case__animal_id__isnull=False,
        )
        .select_related('sale_case')
        .order_by('created_at', 'pk')
        .iterator()
    ):
        Animal.objects.filter(pk=sale.sale_case.animal_id).update(
            sold_at=sale.sold_at,
            sold_to_id=sale.sale_case.user_id,
        )

    legacy_case_ids = list(
        AnimalSale.objects.filter(source='legacy').values_list(
            'sale_case_id',
            flat=True,
        )
    )
    AnimalSale.objects.filter(source='legacy').delete()
    AnimalSaleCase.objects.filter(
        pk__in=legacy_case_ids,
        origin='legacy',
    ).delete()
    AnimalSaleCase.objects.filter(
        pk__in=legacy_case_ids,
        status='sold',
    ).update(
        status='closed',
        closed_at=timezone.now(),
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('breeding', '0016_animal_reservation_offer_hours'),
        ('reservations', '0016_cross_database_unique_guards'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='animalsale',
            name='animal_sale_final_price_gte_zero',
        ),
        migrations.AddField(
            model_name='animalsale',
            name='source',
            field=models.CharField(
                choices=[
                    ('workflow', 'Commercial workflow'),
                    ('legacy', 'Imported legacy record'),
                ],
                db_index=True,
                default='workflow',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='animalsale',
            name='void_reason',
            field=models.TextField(blank=True, verbose_name='void reason'),
        ),
        migrations.AddField(
            model_name='animalsale',
            name='voided_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name='voided at',
            ),
        ),
        migrations.AddField(
            model_name='animalsale',
            name='voided_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='voided_animal_sales',
                to=settings.AUTH_USER_MODEL,
                verbose_name='voided by',
            ),
        ),
        migrations.AlterField(
            model_name='animalsale',
            name='charge',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='sale_stage',
                to='reservations.charge',
            ),
        ),
        migrations.AlterField(
            model_name='animalsale',
            name='final_price',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=9,
                null=True,
                verbose_name='final sale price',
            ),
        ),
        migrations.AlterField(
            model_name='animalsalecase',
            name='origin',
            field=models.CharField(
                choices=[
                    ('online', 'Online'),
                    ('admin', 'Created by staff'),
                    ('transfer', 'Transferred'),
                    ('legacy', 'Imported legacy sale'),
                ],
                db_index=True,
                default='online',
                max_length=20,
                verbose_name='origin',
            ),
        ),
        migrations.AlterField(
            model_name='workflowclosure',
            name='kind',
            field=models.CharField(
                choices=[
                    ('cancelled', 'Cancelled'),
                    ('sale_cancelled', 'Sale cancelled'),
                    ('rejected', 'Not accepted'),
                    ('expired', 'Expired'),
                    ('transferred', 'Transferred'),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name='animalsale',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(final_price__isnull=True)
                    | models.Q(final_price__gte=0)
                ),
                name='animal_sale_final_price_gte_zero',
            ),
        ),
        migrations.AddConstraint(
            model_name='animalsale',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        source='workflow',
                        charge__isnull=False,
                        final_price__isnull=False,
                    )
                    | models.Q(
                        source='legacy',
                        charge__isnull=True,
                        final_price__isnull=True,
                    )
                ),
                name='animal_sale_source_matches_financial_data',
            ),
        ),
        migrations.AddConstraint(
            model_name='animalsale',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(voided_at__isnull=True, void_reason='')
                    | (
                        models.Q(voided_at__isnull=False)
                        & ~models.Q(void_reason='')
                    )
                ),
                name='animal_sale_void_metadata_consistent',
            ),
        ),
        migrations.RunPython(
            migrate_legacy_animal_sales,
            restore_legacy_animal_fields,
        ),
        migrations.RemoveConstraint(
            model_name='animalsalecase',
            name='one_active_sale_case_per_animal',
        ),
        migrations.RemoveField(
            model_name='animalsalecase',
            name='active_animal_key',
        ),
        migrations.AddField(
            model_name='animalsalecase',
            name='blocking_animal_key',
            field=models.GeneratedField(
                db_persist=False,
                expression=models.Case(
                    models.When(
                        status__in=(
                            'pre_reservation',
                            'reservation',
                            'sold',
                        ),
                        then=models.F('animal'),
                    ),
                    default=models.Value(None),
                ),
                null=True,
                output_field=models.BigIntegerField(null=True),
            ),
        ),
        migrations.AddConstraint(
            model_name='animalsalecase',
            constraint=models.UniqueConstraint(
                fields=('blocking_animal_key',),
                name='one_blocking_sale_case_per_animal',
            ),
        ),
    ]
