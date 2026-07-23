from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0013_certification_chat_search_aliases"),
        ("chat", "0004_move_search_aliases_to_index"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="animal",
            name="chat_search_aliases",
        ),
        migrations.RemoveField(
            model_name="animalkind",
            name="chat_search_aliases",
        ),
        migrations.RemoveField(
            model_name="breed",
            name="chat_search_aliases",
        ),
        migrations.RemoveField(
            model_name="certification",
            name="chat_search_aliases",
        ),
        migrations.RemoveField(
            model_name="litter",
            name="chat_search_aliases",
        ),
    ]
