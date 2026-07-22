# Fortissimus Bellator

Fortissimus Bellator is a multilingual Django website for a professional dog
breeder in Portugal. It combines the public kennel site with the operational
content needed to sell dogs and manage upcoming litters.

The main product areas are:

- breed, dog, and litter catalogues with detail and pre-reservation flows;
- FAQs, contact information, an about page, and a kennel blog;
- a breed-matching quiz and customer accounts;
- multilingual content, media attachments, and Django admin management;
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

Admin file and EditorJS image uploads are staff-only and bounded by the
`UPLOAD_*` and `EDITOR_*` environment settings. Remote EditorJS images accept
only public HTTP(S) addresses and are streamed with strict size limits.
