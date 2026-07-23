from django.db import migrations, models


LEGACY_DOG_NAMES = {"dog", "cão", "perro", "chien", "hund", "cane"}
LEGACY_DOG_ALIASES = (
    "Dogs",
    "Puppy",
    "Puppies",
    "Cães",
    "Cachorro",
    "Cachorros",
    "Perros",
    "Chiens",
    "Chiot",
    "Chiots",
    "Hunde",
    "Welpe",
    "Welpen",
    "Cani",
    "Cucciolo",
    "Cuccioli",
)


def add_legacy_dog_aliases(apps, _schema_editor):
    """Keep the former built-in dog vocabulary after making kinds dynamic."""
    AnimalKind = apps.get_model("breeding", "AnimalKind")
    translated_name_fields = (
        "name",
        "name_en",
        "name_pt",
        "name_es",
        "name_fr",
        "name_de",
        "name_it",
    )

    for animal_kind in AnimalKind.objects.all():
        names = {
            str(getattr(animal_kind, field_name, "") or "").strip().casefold()
            for field_name in translated_name_fields
        }
        if not names.intersection(LEGACY_DOG_NAMES):
            continue

        existing_aliases = [
            alias.strip()
            for alias in animal_kind.chat_search_aliases.splitlines()
            if alias.strip()
        ]
        known_aliases = {alias.casefold() for alias in existing_aliases}
        new_aliases = [
            alias
            for alias in LEGACY_DOG_ALIASES
            if alias.casefold() not in known_aliases
        ]
        if new_aliases:
            animal_kind.chat_search_aliases = "\n".join(
                (*existing_aliases, *new_aliases)
            )
            animal_kind.save(update_fields=["chat_search_aliases"])


class Migration(migrations.Migration):
    dependencies = [
        ("breeding", "0011_litter_expecting_litter_reservation_capacity_zero"),
    ]

    operations = [
        migrations.AddField(
            model_name="animalkind",
            name="chat_search_aliases",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Optional singular, plural, or colloquial names, one per "
                    "line. Used only by chat search."
                ),
                verbose_name="chat search aliases",
            ),
        ),
        migrations.RunPython(
            add_legacy_dog_aliases,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
