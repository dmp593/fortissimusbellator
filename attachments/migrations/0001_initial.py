# Generated by Django 5.1.6 on 2025-03-14 00:22

import attachments.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='Attachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to=attachments.models.attachment_file_upload_to, verbose_name='file')),
                ('thumbnail', models.ImageField(blank=True, null=True, upload_to=attachments.models.attachment_thumbnail_upload_to, verbose_name='thumbnail')),
                ('object_id', models.PositiveIntegerField()),
                ('description', models.CharField(blank=True, max_length=255, verbose_name='description')),
                ('description_en', models.CharField(blank=True, max_length=255, null=True, verbose_name='description')),
                ('description_pt', models.CharField(blank=True, max_length=255, null=True, verbose_name='description')),
                ('filename', models.CharField(editable=False, max_length=255, verbose_name='filename')),
                ('mime_type', models.CharField(editable=False, max_length=50, verbose_name='MIME type')),
                ('order', models.IntegerField(default=999, verbose_name='order')),
                ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', related_query_name='attachment', to='contenttypes.contenttype')),
            ],
            options={
                'verbose_name': 'attachment',
                'verbose_name_plural': 'attachments',
                'ordering': ['order'],
                'indexes': [models.Index(fields=['content_type', 'object_id'], name='attachments_content_44d952_idx'), models.Index(fields=['mime_type'], name='attachments_mime_ty_038735_idx')],
            },
        ),
    ]
