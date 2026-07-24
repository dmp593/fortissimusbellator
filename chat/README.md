# Local chat module

The chat widget answers short sales, litter, breed, blog, and FAQ questions.
It uses live Django data for website facts and one small local GGUF model only
when a question has related published knowledge but no deterministic answer.
It never stores conversations in the database.

This file is the short setup and operating guide. The exhaustive pipeline,
security, matching, search index, intent/expert, local-model, alias, file map,
extension, and troubleshooting reference is
[`docs/chat.md`](../docs/chat.md). The complete project documentation starts at
[`docs/README.md`](../docs/README.md).

## Design

`ChatService` is the stable entry point (Facade). Its small collaborators each
have one job:

- `views.py` validates the HTTP payload and bounds the browser-supplied history;
- `intents.py` detects supported intentions;
- `search_registry.py` explicitly declares the model types chat may search;
- `search_index.py` maintains their central ContentTypes-backed projection;
- `entities.py` ranks indexed terms and reloads matches through public queries;
- `matching.py` provides accent-insensitive fuzzy matching with `difflib`;
- `catalog.py` contains the public Django queries, including published posts;
- `experts.py` contains deterministic answer strategies;
- `knowledge.py` selects compact facts for the model fallback;
- `response_policy.py` rejects model URLs absent from the supplied facts;
- `models.py` stores the GGUF catalogue and polymorphic search entries;
- `model_catalog.py` validates and exposes an immutable runtime model value;
- `model_selection.py` reads and persists the website's active choice;
- `runtime.py` composes one process-owned model and its injected services;
- `assistant.py` owns model download, loading, context fitting, and inference.

The router asks every relevant deterministic expert so a compound request can
combine, for example, available animals and current litters. If none can answer,
the model is invoked exactly once only when related non-FAQ published knowledge
was found. Otherwise a deterministic knowledge-boundary response is returned.
This prevents a small local model from answering unrelated general-knowledge
questions. There is deliberately no model call for intent classification: on a
two-vCPU host a second inference would increase latency and queue contention
without making database facts more reliable.

An unambiguous FAQ word match returns the stored translated answer verbatim.
Reviewed aliases require a strong full-phrase match, preventing a word in an
alias for another language from triggering the wrong FAQ. If several FAQs are
relevant, their published questions and answers are returned without model
rewriting. FAQ content is never supplied to the response-generating model.
The model prompt may repeat only exact URLs present in its knowledge snapshot,
and `response_policy.py` enforces that rule after inference. A response
containing an invented internal path or external URL is discarded and replaced
by the deterministic knowledge-boundary answer.

Certification shortcuts and named certification questions are deterministic:
they render the current database records directly and never invoke the model.

Canonical names and reviewed aliases are held in one `ChatSearchEntry` table.
Each row uses Django ContentTypes to point to an animal, animal kind, litter,
breed, certification, or FAQ. The resolver reads that projection in one query,
ranks names and aliases globally, and only then bulk-loads matches through each
type's live public queryset. The index can therefore never override visibility,
availability, or publication rules.

Normal saves and deletes synchronize this derived projection. Bulk imports or
queryset updates can refresh it explicitly:

```bash
python manage.py rebuild_chat_search_index
```

The registry defines canonical terms explicitly; `__str__()` is only the short
human-readable label. Exact matches win and small spelling mistakes use
conservative fuzzy matching, with no vector database or embedding dependency.

`EntityKind` describes technical model types such as `ANIMAL` and
`ANIMAL_KIND`; it never contains one enum member per category. Animal
categories and their translated names come from `AnimalKind` records.
Administrators still edit alternative names or questions beside the original
entity in Django admin. This is a virtual field that writes to the central
entry, rather than a duplicated column on six models. Adding cats or another
category therefore requires database configuration, not a new enum, intent, or
expert. Enter one alias per line.

Sales and pre-reservation answers are also live database facts. Dogs held by an
active pre-reservation or a confirmed reservation are excluded from available
inventory. Failed, expired, rejected, and cancelled workflows release the dog.
Litters are never sold or pre-reserved; the chat directs customers to birth
alerts and to individual dogs published after birth. Named-entity, price, and
catalogue answers all use `reservations/availability.py`, so they cannot
contradict one another.

## Session and context

`assets/js/components/chat.js` keeps completed turns and the last unambiguous
entity reference in `sessionStorage`. This lets a follow-up such as “How much
does she cost?” refer to the animal from the previous answer. Resetting the
widget or closing the browser tab ends the session. The server stores no chat
history.

The browser sends the current page title, route, path, and any public detail
identifier. The server accepts only its explicit allow-list and looks entities
up again through public querysets; it never trusts browser data as catalogue
facts.

## Model setup

The seed catalogue contains Qwen3.5 0.8B Instruct Q4_K_M, Qwen2.5 0.5B
Instruct Q4_K_M, LFM2 1.2B Q4_K_M, Gemma 3 1B IT Q4_K_M, and Llama 3.2 1B
Instruct Q3_K_L. Load it once after migrating:

```bash
python manage.py loaddata chat_models
```

The seed aliases live in a separate polymorphic fixture. On a fresh
fixture-based setup, load it after the referenced entities:

```bash
python manage.py loaddata \
  animalskinds breeds certifications animals litters faqs \
  chat_search_entries
```

The data migration preserves aliases already present in an existing database,
so existing installations must not reload this seed fixture.

After that, staff can add, edit, disable, or remove catalogue entries through
**Local chat models** in Django admin. A record stores a Hugging Face repository,
revision, and GGUF filename; arbitrary hosts and local paths are deliberately
not accepted. No `hf` command or production terminal access is required.

The SHA-256 field is optional. Leave it blank when a revision such as `main`
should be allowed to change upstream. Every completed download still logs the
observed SHA-256. Fill it when exact reproducibility is required; in that mode,
a mismatch prevents the file from being published or loaded. An explicit
**Download latest version** action replaces an existing file from the current
repository revision. Downloads use a `.part` file and are published atomically.

All model files live below `CHAT_MODEL_DIR`. The directory must be writable by
the web process and use persistent storage in production. `.models/` is ignored
by Git. WSGI and ASGI start preparation as soon as the web process boots.
Download and model loading run in one background thread; deterministic FAQ and
catalogue answers remain available. A fallback request that overlaps normal
loading waits up to `CHAT_MODEL_WAIT_SECONDS` instead of failing immediately.

Staff can select, download, activate, update, retry, and inspect progress from
**Local chat model status** in the Django admin header. Changing the selection
safely unloads the resident model before loading the replacement, and the
choice is stored in the database. Its localized URL is
`/<language>/admin/chat/model-status/`. The legacy `/chat/model-status/` URL
remains available for existing bookmarks. Neither page exposes model paths or
errors to public users.

Environment settings:

- `CHAT_MODEL_DIR`: persistent directory shared by all catalogue files
  (default `.models`);
- `CHAT_MODEL_AUTO_DOWNLOAD`: automatic fallback (default `true`);
- `CHAT_MODEL_DOWNLOAD_TIMEOUT`: network read timeout in seconds;
- `CHAT_MODEL_MAX_DOWNLOAD_BYTES`: hard download-size limit;
- `CHAT_CONTEXT_SIZE`, `CHAT_MAX_OUTPUT_TOKENS`, and `CHAT_THREADS`: inference
  limits.

`CHAT_MAX_BLOG_POSTS` limits the number of published titles included in a
deterministic blog reply and in model knowledge. `CHAT_MAX_CERTIFICATIONS`
bounds the certification catalogue supplied to the model.

Production storage example:

```text
CHAT_MODEL_DIR=/persistent/path/chat-models
```

Set `CHAT_MODEL_AUTO_DOWNLOAD=false` when the model ships with the deployment.
If preparation fails, the user sees the normal unavailable message and the
application log records the cause. A staff user can retry it from the status
page without restarting the process.

## Two-GB deployment rule

The defaults use a 2,048-token context, 192 output tokens, two CPU threads, a
small batch, memory mapping, and one inference lock. `CHAT_CONTEXT_SIZE`,
`CHAT_MAX_OUTPUT_TOKENS`, and `CHAT_THREADS` intentionally remain system-wide
deployment limits, not model- or expert-specific settings. They protect RAM,
response time, and CPU concurrency on the two-GB host; the deterministic
experts do not run separate model instances.

Run exactly one application process. Every WSGI/ASGI worker is a separate
process and would load another copy of the model. Use threads for ordinary
Django concurrency, knowing that model requests are intentionally serialized.

The loaded model remains resident until that process exits or staff activates
another model. There is no idle-unload timer. Production must disable
scale-to-zero and keep at least one web instance running; application code
cannot keep memory alive after the hosting platform stops or recycles a
process. After any restart, boot warm-up runs again and the persistent model
volume avoids another download.

Mutable runtime services are not module globals. Django's `ChatConfig` instance
owns the process runtime, and views/admin resolve it through the app registry.
The runtime contains no customer history or identity; request and conversation
state remain local to each request or in browser `sessionStorage`.

## Operational logs

The module emits structured `key=value` events for selected experts, detected
intents, entity IDs and confidence scores, model progress, and duration. Raw
messages, session history, and client addresses are deliberately excluded.

## AI-assisted search aliases

Animal kinds, animals, litters, breeds, certifications, and FAQs expose a
**Generate with AI** action beside their chat-alias field on their Django admin
change form. The action sends only an explicit set of public names, codes, or
questions to the active local model. Suggestions are validated, deduplicated,
checked against other indexed records, and added to the browser form without
being saved. An administrator must review the textarea and use the normal save
action before any suggestion becomes searchable.

Blog posts are deliberately excluded. Their body is structured editor JSON,
and the chat currently exposes only real published titles. The weak local model
must never generate or rewrite that JSON.

AI generation is deliberately separate from model saves, index synchronization,
and the public `ChatService`. A missing, busy, or invalid local model therefore
cannot prevent normal admin edits. New records must first be saved with **Save
and continue editing** so the staff-only, CSRF-protected suggestion endpoint can
enforce object-level change permission.

## Verification

The tests mock inference, so the GGUF file is not required:

```bash
python manage.py test chat
```
