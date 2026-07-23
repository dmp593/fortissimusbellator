from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0012_animalkind_chat_search_aliases"),
    ]

    operations = [
        migrations.AddField(
            model_name="certification",
            name="chat_search_aliases",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Optional alternative codes, names, or questions, one per "
                    "line. Used only by chat search."
                ),
                verbose_name="chat search aliases",
            ),
        ),
    ]
