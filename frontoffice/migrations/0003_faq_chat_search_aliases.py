from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("frontoffice", "0002_frequentlyaskedquestion_answer_de_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="frequentlyaskedquestion",
            name="chat_search_aliases",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Optional alternative questions, one per line. "
                    "Used only by chat search."
                ),
                verbose_name="chat search aliases",
            ),
        ),
    ]
