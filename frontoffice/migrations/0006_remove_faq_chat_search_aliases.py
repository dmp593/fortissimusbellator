from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0004_move_search_aliases_to_index"),
        ("frontoffice", "0005_update_pre_reservation_faqs"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="frequentlyaskedquestion",
            name="chat_search_aliases",
        ),
    ]
