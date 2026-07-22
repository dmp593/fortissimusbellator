import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ChatModel",
            fields=[
                (
                    "model_id",
                    models.SlugField(max_length=64, primary_key=True, serialize=False),
                ),
                ("name", models.CharField(max_length=120)),
                (
                    "repository",
                    models.CharField(
                        help_text="Hugging Face repository in owner/name format.",
                        max_length=180,
                    ),
                ),
                ("filename", models.CharField(max_length=255, unique=True)),
                (
                    "revision",
                    models.CharField(
                        default="main",
                        help_text=(
                            "Branch, tag, or commit. Use main to allow upstream "
                            "updates."
                        ),
                        max_length=100,
                    ),
                ),
                (
                    "sha256",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Optional. When present, downloaded bytes must match "
                            "exactly."
                        ),
                        max_length=64,
                    ),
                ),
                (
                    "download_size",
                    models.PositiveBigIntegerField(
                        default=0,
                        help_text=(
                            "Optional estimated size in bytes; zero means unknown."
                        ),
                    ),
                ),
                ("summary", models.CharField(blank=True, max_length=255)),
                ("recommended", models.BooleanField(default=False)),
                ("enabled", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "local chat model",
                "verbose_name_plural": "local chat models",
                "ordering": ("-recommended", "name"),
            },
        ),
        migrations.CreateModel(
            name="ChatModelConfiguration",
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
                (
                    "active_model",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="chat.chatmodel",
                    ),
                ),
            ],
            options={
                "verbose_name": "chat model configuration",
                "verbose_name_plural": "chat model configuration",
            },
        ),
    ]
