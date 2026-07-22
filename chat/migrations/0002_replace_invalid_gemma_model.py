from django.db import migrations


OLD_MODEL_ID = "gemma-3-1b-it-q3-k-m"
NEW_MODEL_ID = "gemma-3-1b-it-q4-k-m"


def replace_invalid_gemma_model(apps, schema_editor):
    """Replace the fixture entry that pointed to a non-existent GGUF file."""
    ChatModel = apps.get_model("chat", "ChatModel")
    ChatModelConfiguration = apps.get_model("chat", "ChatModelConfiguration")

    invalid_model = ChatModel.objects.filter(model_id=OLD_MODEL_ID).first()
    if invalid_model is None:
        return

    ChatModel.objects.update_or_create(
        model_id=NEW_MODEL_ID,
        defaults={
            "name": "Gemma 3 1B IT · Q4_K_M",
            "repository": "ggml-org/gemma-3-1b-it-GGUF",
            "filename": "gemma-3-1b-it-Q4_K_M.gguf",
            "revision": "main",
            "sha256": "",
            "download_size": 806_000_000,
            "summary": (
                "Multilingual 1B option in a higher-quality Q4 quantization."
            ),
            "recommended": False,
            "enabled": invalid_model.enabled,
        },
    )
    ChatModelConfiguration.objects.filter(
        active_model_id=OLD_MODEL_ID
    ).update(active_model_id=NEW_MODEL_ID)
    invalid_model.delete()


class Migration(migrations.Migration):
    dependencies = [("chat", "0001_chatmodelconfiguration")]

    operations = [
        migrations.RunPython(
            replace_invalid_gemma_model,
            migrations.RunPython.noop,
        ),
    ]
