# Generated by Django 5.1.6 on 2025-03-14 17:38

import django.db.models.deletion
import django.db.models.manager
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('breeding', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='animal',
            options={'ordering': ['order'], 'verbose_name': 'animal', 'verbose_name_plural': 'animals'},
        ),
        migrations.AlterModelManagers(
            name='animal',
            managers=[
                ('dogs_for_sale', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterField(
            model_name='animal',
            name='father',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children_father', related_query_name='child_father', to='breeding.animal', verbose_name='father'),
        ),
        migrations.AlterField(
            model_name='animal',
            name='mother',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children_mother', related_query_name='child_mother', to='breeding.animal', verbose_name='mother'),
        ),
        migrations.CreateModel(
            name='Litter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, verbose_name='name')),
                ('description', models.TextField(blank=True, verbose_name='description')),
                ('description_en', models.TextField(blank=True, null=True, verbose_name='description')),
                ('description_pt', models.TextField(blank=True, null=True, verbose_name='description')),
                ('expected_birth_date', models.DateField(blank=True, null=True, verbose_name='expected birth date')),
                ('expected_delivery_date', models.DateField(blank=True, null=True, verbose_name='expected delivery date')),
                ('expected_babies', models.PositiveIntegerField(blank=True, null=True, verbose_name='expected number of babies')),
                ('active', models.BooleanField(default=True, verbose_name='active')),
                ('order', models.IntegerField(default=999, verbose_name='order')),
                ('breed', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='litters', related_query_name='litter', to='breeding.breed', verbose_name='breed')),
                ('father', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='litter_father', related_query_name='litter_father', to='breeding.animal', verbose_name='father')),
                ('mother', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='litter_mother', related_query_name='litter_mother', to='breeding.animal', verbose_name='mother')),
            ],
            options={
                'verbose_name': 'litter',
                'verbose_name_plural': 'litters',
                'ordering': ['order'],
            },
        ),
        migrations.DeleteModel(
            name='AnimalFile',
        ),
    ]
