from django.db import migrations


ENTITY_FIELDS = (
    ("breeding", "Animal", "name", ("name",)),
    (
        "breeding",
        "AnimalKind",
        "name",
        ("name", "name_en", "name_pt", "name_es", "name_fr", "name_de", "name_it"),
    ),
    (
        "breeding",
        "Breed",
        "name",
        ("name", "name_en", "name_pt", "name_es", "name_fr", "name_de", "name_it"),
    ),
    ("breeding", "Litter", "name", ("name",)),
    ("breeding", "Certification", "code", ("code", "name")),
    (
        "frontoffice",
        "FrequentlyAskedQuestion",
        "question",
        (
            "question",
            "question_en",
            "question_pt",
            "question_es",
            "question_fr",
            "question_de",
            "question_it",
        ),
    ),
)


def move_aliases_to_index(apps, _schema_editor):
    ChatSearchEntry = apps.get_model("chat", "ChatSearchEntry")
    ContentType = apps.get_model("contenttypes", "ContentType")

    for app_label, model_name, label_field, term_fields in ENTITY_FIELDS:
        Model = apps.get_model(app_label, model_name)
        content_type, _created = ContentType.objects.get_or_create(
            app_label=app_label,
            model=Model._meta.model_name,
        )
        for instance in Model.objects.all().iterator():
            terms = _unique_values(
                getattr(instance, field_name, "")
                for field_name in term_fields
            )
            ChatSearchEntry.objects.update_or_create(
                content_type=content_type,
                object_id=instance.pk,
                defaults={
                    "label": str(
                        getattr(instance, label_field, "") or instance.pk
                    )[:255],
                    "canonical_terms": terms,
                    "aliases": instance.chat_search_aliases,
                },
            )


def remove_index_entries(apps, _schema_editor):
    ChatSearchEntry = apps.get_model("chat", "ChatSearchEntry")
    ContentType = apps.get_model("contenttypes", "ContentType")
    content_type_ids = ContentType.objects.filter(
        app_label__in={"breeding", "frontoffice"},
        model__in={
            "animal",
            "animalkind",
            "breed",
            "litter",
            "certification",
            "frequentlyaskedquestion",
        },
    ).values_list("pk", flat=True)
    ChatSearchEntry.objects.filter(
        content_type_id__in=content_type_ids,
    ).delete()


def _unique_values(values):
    unique = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        normalized = value.casefold()
        if value and normalized not in seen:
            unique.append(value)
            seen.add(normalized)
    return unique


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0013_certification_chat_search_aliases"),
        ("chat", "0003_chatsearchentry"),
        ("frontoffice", "0005_update_pre_reservation_faqs"),
    ]

    operations = [
        migrations.RunPython(
            move_aliases_to_index,
            reverse_code=remove_index_entries,
        ),
    ]
