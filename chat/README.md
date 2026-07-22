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
- `assistant.py` owns model download, loading, context fitting, and inference.

The router asks every relevant deterministic expert so a compound request can
combine, for example, available dogs and current litters. If none can answer,
it invokes the model expert exactly once. There is deliberately no model call
for intent classification: on a two-vCPU host a second inference would increase
latency and queue contention without making database facts more reliable.

Entity names are read from the database on each request. Exact matches win;
small spelling mistakes use conservative fuzzy matching. This avoids an entity
cache that could become stale and adds no vector database, embedding model, or
large runtime dependency.

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

The default model is Qwen2.5 1.5B Instruct Q4_K_M. No `hf` command or terminal
access is required. If the file at `CHAT_MODEL_PATH` is missing, the first
fallback question starts one background download and asks the user to retry in
a few minutes. Deterministic FAQ and catalogue answers remain available while
the model is downloading.

The download is written beside `CHAT_MODEL_PATH` with a `.part` suffix and is
moved to the configured path only after it is complete. The model directory
must be writable by the web process and should use persistent storage.
`.models/` is intentionally ignored by Git.

Environment settings:

- `CHAT_MODEL_PATH`: final GGUF path (default
  `.models/qwen2.5-1.5b-instruct-q4_k_m.gguf`);
- `CHAT_MODEL_AUTO_DOWNLOAD`: automatic fallback (default `true`);
- `CHAT_MODEL_DOWNLOAD_URL`: GGUF source URL;
- `CHAT_MODEL_DOWNLOAD_TIMEOUT`: network read timeout in seconds;
- `CHAT_CONTEXT_SIZE`, `CHAT_MAX_OUTPUT_TOKENS`, and `CHAT_THREADS`: inference
  limits.

Example:

```text
CHAT_MODEL_PATH=/persistent/path/qwen2.5-1.5b-instruct-q4_k_m.gguf
CHAT_MODEL_DOWNLOAD_URL=https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

Set `CHAT_MODEL_AUTO_DOWNLOAD=false` when the model ships with the deployment.
If an automatic download fails, the user sees the normal unavailable message
and the application log records the cause; restart the process to retry.

## Two-GB deployment rule

The defaults use a 2,048-token context, 192 output tokens, two CPU threads, a
small batch, memory mapping, and one inference lock. Run exactly one application
process. Every WSGI/ASGI worker is a separate process and would load another
copy of the model. Use threads for ordinary Django concurrency, knowing that
model requests are intentionally serialized.

## Verification

The tests mock inference, so the GGUF file is not required:

```bash
python manage.py test chat
```
