# Fortissimus Bellator

Fortissimus Bellator is a multilingual Django website for a professional dog
breeder in Portugal. It combines the public kennel site with the operational
content needed to sell dogs and manage upcoming litters.

The main product areas are:

- breed, dog, and litter catalogues with detail and pre-reservation flows;
- FAQs, contact information, an about page, and a kennel blog;
- a breed-matching quiz and customer accounts;
- multilingual content, media attachments, and Django admin management;
- Stripe-backed dog and born-litter pre-reservations with fiscal documents;
- a small local sales assistant documented in [`chat/README.md`](chat/README.md).

## Local development

```bash
poetry install
npm install
poetry run python manage.py migrate
poetry run python manage.py loaddata chat_models
npm run prod
poetry run python manage.py runserver
```

The default database is SQLite. Copy the required deployment values into a
local `.env`; Django reads that file automatically when it exists. See the chat
module documentation for the optional local GGUF model setup.

For container-based development or deployment, build the image and apply
migrations before starting both application services:

```bash
docker compose build
docker compose run --rm web python manage.py migrate
docker compose up -d
```

The Compose stack runs MariaDB, Gunicorn, and one reservation reconciliation
scheduler. Replace every default secret before exposing it outside localhost.

Pre-reservation deployment, Stripe webhook, TOConline, and reconciliation
instructions are documented in
[`docs/pre-reservations.md`](docs/pre-reservations.md).

Admin file and EditorJS image uploads are staff-only and bounded by the
`UPLOAD_*` and `EDITOR_*` environment settings. Remote EditorJS images accept
only public HTTP(S) addresses and are streamed with strict size limits.
