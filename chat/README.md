# Local chat module

The chat widget answers short sales, litter, breed, and FAQ questions. It uses
live Django data for website facts and one small local GGUF model only when no
deterministic answer is available. It never stores conversations in the
database.

## Design

`ChatService` is the stable entry point (Facade). Its small collaborators each
have one job:

- `views.py` validates the HTTP payload and bounds the browser-supplied history;
- `intents.py` detects supported intentions;
- `entities.py` resolves live dog, litter, and breed names;
- `matching.py` provides accent-insensitive fuzzy matching with `difflib`;
- `catalog.py` contains the public Django queries;
- `experts.py` contains deterministic answer strategies;
- `knowledge.py` selects compact facts for the model fallback;
- `models.py` stores the administrator-managed GGUF catalogue;
- `model_catalog.py` validates and exposes an immutable runtime model value;
- `model_selection.py` reads and persists the website's active choice;
- `assistant.py` owns model download, loading, context fitting, and inference.

The router asks every relevant deterministic expert so a compound request can
combine, for example, available dogs and current litters. If none can answer,
it invokes the model expert exactly once. There is deliberately no model call
for intent classification: on a two-vCPU host a second inference would increase
latency and queue contention without making database facts more reliable.

Entity names are read from the database on each request. Exact matches win;
small spelling mistakes use conservative fuzzy matching. This avoids an entity
cache that could become stale and adds no vector database, embedding model, or
large runtime dependency. Administrators can add alternative names or questions
through each dog's, litter's, breed's, or FAQ's **chat search aliases** field.
Enter one alias per line; no Python change is required.

## Session and context

`assets/js/components/chat.js` keeps completed turns and the last unambiguous
dog/litter/breed reference in `sessionStorage`. This lets a follow-up such as
“How much does she cost?” refer to the dog from the previous answer. Resetting
the widget or closing the browser tab ends the session. The server stores no
chat history.

The browser sends the current page title, route, path, and any public detail
identifier. The server accepts only its explicit allow-list and looks entities
up again through public querysets; it never trusts browser data as catalogue
facts.

## Model setup

The seed catalogue contains Qwen3.5 0.8B Instruct Q4_K_M and Qwen2.5 0.5B
Instruct Q4_K_M. Load it once after migrating:

```bash
python manage.py loaddata chat_models
```

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
by Git. If a selected file is missing, the first fallback question can start
preparation and ask the visitor to retry. Download and model loading run in one
background thread; deterministic FAQ and catalogue answers remain available.

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

## Operational logs

The module emits structured `key=value` events for selected experts, detected
intents, entity IDs and confidence scores, model progress, and duration. Raw
messages, session history, and client addresses are deliberately excluded.

## Verification

The tests mock inference, so the GGUF file is not required:

```bash
python manage.py test chat
```
