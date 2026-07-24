from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('breeding', '0016_animal_reservation_offer_hours'),
        ('reservations', '0017_canonical_animal_sales'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='animal',
            name='sold_at',
        ),
        migrations.RemoveField(
            model_name='animal',
            name='sold_to',
        ),
    ]
