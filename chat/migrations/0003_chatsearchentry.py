from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_replace_invalid_gemma_model"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatSearchEntry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("object_id", models.PositiveBigIntegerField()),
                ("label", models.CharField(max_length=255)),
                ("canonical_terms", models.JSONField(default=list)),
                (
                    "aliases",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "Optional alternative names or questions, one per "
                            "line. Used only by chat search."
                        ),
                        verbose_name="chat search aliases",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "content_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_search_entries",
                        to="contenttypes.contenttype",
                    ),
                ),
            ],
            options={
                "verbose_name": "chat search entry",
                "verbose_name_plural": "chat search entries",
                "ordering": ("label",),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("content_type", "object_id"),
                        name="unique_chat_search_object",
                    ),
                ],
            },
        ),
    ]
