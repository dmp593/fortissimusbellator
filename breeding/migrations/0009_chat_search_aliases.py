from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0008_site_domain"),
    ]

    operations = [
        migrations.AddField(
            model_name="animal",
            name="chat_search_aliases",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Optional alternative names, one per line. "
                    "Used only by chat search."
                ),
                verbose_name="chat search aliases",
            ),
        ),
        migrations.AddField(
            model_name="breed",
            name="chat_search_aliases",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Optional alternative names, one per line. "
                    "Used only by chat search."
                ),
                verbose_name="chat search aliases",
            ),
        ),
        migrations.AddField(
            model_name="litter",
            name="chat_search_aliases",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Optional alternative names, one per line. "
                    "Used only by chat search."
                ),
                verbose_name="chat search aliases",
            ),
        ),
    ]
