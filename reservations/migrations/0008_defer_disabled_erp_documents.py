from django.db import migrations, models


DISABLED_ERROR = 'ValueError: TOConline integration is disabled.'


def defer_disabled_documents(apps, schema_editor):
    ERPDocument = apps.get_model('reservations', 'ERPDocument')
    ERPDocument.objects.filter(
        status__in=('needs_attention', 'retryable_failure'),
        erp_document_id__isnull=True,
        last_error=DISABLED_ERROR,
    ).update(
        status='deferred',
        processing_started_at=None,
        next_retry_at=None,
        last_error='',
    )


def restore_deferred_documents(apps, schema_editor):
    ERPDocument = apps.get_model('reservations', 'ERPDocument')
    ERPDocument.objects.filter(status='deferred').update(status='pending')


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0007_payment_checkout_attempt_number'),
    ]

    operations = [
        migrations.AlterField(
            model_name='erpdocument',
            name='status',
            field=models.CharField(
                choices=[
                    ('deferred', 'Integration deferred'),
                    ('pending', 'Pending'),
                    ('processing', 'Processing'),
                    ('integrated', 'Integrated'),
                    ('retryable_failure', 'Retryable failure'),
                    ('needs_attention', 'Needs attention'),
                ],
                db_index=True,
                default='pending',
                max_length=30,
            ),
        ),
        migrations.RunPython(
            defer_disabled_documents,
            restore_deferred_documents,
        ),
    ]
