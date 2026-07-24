# Operations

## Runtime components

A complete deployment has:

| Component | Required | Responsibility |
| --- | --- | --- |
| Django/Gunicorn web process | Yes | Public site, admin, customer workflows, webhook |
| Relational database | Yes | All durable content and commercial state |
| Persistent media storage | Yes | Uploaded images, videos, and files |
| Reconciliation scheduler | Yes for commercial operation | Payments, refunds, offers, ERP, PDFs, birth emails |
| SMTP service | Yes for customer operation | Activation, password, contact, commercial, and alert email |
| Stripe | Optional until online payments enabled | Hosted checkout and online refunds |
| TOConline | Optional | Fiscal sales documents, credit notes, PDFs |
| Persistent GGUF model storage | Optional | Local chat model |

The scheduler uses the same image, settings, database, and media environment as
the web process.

## Local development

### Prerequisites

- Python 3.13;
- Poetry 2;
- Node.js/npm compatible with the lockfile;
- compiler/native dependencies required by `llama-cpp-python`, MySQL client,
  Pillow, and OpenCV;
- Stripe CLI only when testing webhooks;
- a local SMTP capture service or console/test backend when testing email.

### First setup

```bash
poetry install
npm install
cp .env.example .env
poetry run python manage.py migrate
poetry run python manage.py loaddata chat_models
npm run prod
poetry run python manage.py createsuperuser
poetry run python manage.py runserver
```

The default database is `db.sqlite3`. Do not use production secrets in a local
`.env`.

### Initial fixture setup

Fixture-based demo data has dependencies. On a fresh database:

```bash
poetry run python manage.py loaddata \
  animalskinds \
  breeds \
  certifications \
  animals \
  litters \
  faqs \
  chat_models \
  chat_search_entries \
  pre_reservation_terms_v3 \
  reservation_terms_v2
```

The quiz and blog categories may be loaded separately:

```bash
poetry run python manage.py loaddata quiz blog/categories
```

Do not reload `chat_search_entries` or terms fixtures over a live installation:

- the chat migration/rebuild preserves reviewed aliases;
- used terms are contractual history and must be superseded by a new version.

### cPanel deployment

The repository root contains a checked-in [`.cpanel.yml`](../.cpanel.yml).
When cPanel deploys a clean commit, it runs these tasks in order:

1. apply pending database migrations without prompting;
2. compile translation catalogues;
3. collect static files without prompting.

This configuration assumes that cPanel's Repository Path is also the Django
application root. It does not copy the checkout to `public_html`. If the
runtime application uses a different directory, define and review an explicit
deployment path instead of copying the whole repository or using wildcards.

The deployment intentionally does not load fixtures. Fixtures modify business
data and remain an explicit initial-setup operation using the commands above.

Before the first deployment:

- install Poetry and the locked Python dependencies on the cPanel account;
- configure the production `.env`, database, `STATIC_ROOT`, and `MEDIA_ROOT`;
- make `poetry` available on the deployment user's `PATH`;
- build and commit `assets/css/styles.css` with `npm run prod`;
- compile and commit changed translation catalogues before pushing.

The last two steps keep the cPanel-managed Git working tree clean after the
deployment tasks. A dirty working tree prevents cPanel from deploying a later
commit. If deployment fails, inspect cPanel's
`~/.cpanel/logs/vc_TIMESTAMP_git_deploy.log`.

Dependency installation is not performed on every deployment because native
packages such as `llama-cpp-python` are expensive to rebuild on shared hosting.
Run `poetry install --only main --no-root` explicitly when `poetry.lock`
changes.

Immutable container deployments continue to build translations and static
assets into the image and run migrations as a release operation.

### Frontend development

Watch Tailwind:

```bash
npm run dev
```

Build the minified production CSS:

```bash
npm run prod
```

When `DEBUG=false`, collect versioned static assets:

```bash
poetry run python manage.py collectstatic --noinput
```

If a visual change appears missing, rebuild CSS and collect static before
debugging template logic.

## Environment variables

`.env.example` contains local-safe placeholders. The tables below describe
runtime meaning.

### Core Django and database

| Variable | Default | Required in production | Notes |
| --- | --- | --- | --- |
| `SECRET_KEY` | Insecure development value | Yes | Long unpredictable secret; rotate with a session/token plan |
| `DEBUG` | `false` in settings | Yes | Must be off in production |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | Yes | Compose uses MariaDB URL |
| `ALLOWED_HOSTS` | `*` | Yes | Use explicit public/admin hosts |
| `PUBLIC_SITE_URL` | Derived from site domain | Yes | Absolute origin for background/email links, no path |
| `STATIC_ROOT` | `<repo>/static` | Yes | Generated files, not source |
| `MEDIA_ROOT` | `<repo>/media` | Yes | Must be persistent |

`PUBLIC_SITE_URL` examples:

```dotenv
PUBLIC_SITE_URL=http://localhost:8000
PUBLIC_SITE_URL=https://fortissimusbellator.pt
```

### HTTPS and proxy security

| Variable | Local default | Production |
| --- | --- | --- |
| `SECURE_SSL_REDIRECT` | `false` | `true` behind HTTPS |
| `SESSION_COOKIE_SECURE` | `false` | `true` |
| `CSRF_COOKIE_SECURE` | `false` | `true` |
| `TRUST_PROXY_HTTPS` | `false` | `true` only behind a trusted proxy setting `X-Forwarded-Proto` |

Do not enable `TRUST_PROXY_HTTPS` when arbitrary clients can set the forwarding
header. HSTS is configured for one year with subdomains and preload; deploy
HTTPS correctly before exposing the production host.

`X_FRAME_OPTIONS` intentionally remains `SAMEORIGIN` for the staff-only
attachment preview. Public pages also send CSP `frame-ancestors 'none'`, so
external sites cannot frame the application. This intentional exception is the
single expected `manage.py check --deploy` warning.

### Email and business notifications

| Variable | Purpose |
| --- | --- |
| `EMAIL_HOST` | SMTP host |
| `EMAIL_PORT` | SMTP port |
| `EMAIL_HOST_USER` | SMTP username |
| `EMAIL_HOST_PASSWORD` | SMTP password |
| `EMAIL_USE_TLS` | STARTTLS |
| `EMAIL_USE_SSL` | Implicit TLS |
| `DEFAULT_FROM_EMAIL` | Branded sender |
| `BUSINESS_NOTIFICATION_RECIPIENTS` | Comma-separated operational recipients |

Use either TLS mode appropriate to the provider, not both.

`RECIPIENT_LIST_ON_CONTACT_US_REQUEST` is a temporary legacy fallback. New
deployments must use `BUSINESS_NOTIFICATION_RECIPIENTS`.

Business recipients receive contact requests and relevant commercial/ERP
alerts. Customer messages use the shared branded text/HTML renderer and
business `Reply-To`.

### Stripe

| Variable | Required | Purpose |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | When online payment/refund is enabled | Server SDK credential |
| `STRIPE_WEBHOOK_SECRET` | When webhook is enabled | Endpoint signing secret |
| `RESERVATION_CHECKOUT_MINUTES` | Yes | Checkout expiry policy |
| `RESERVATION_REFUND_MAX_AUTOMATIC_ATTEMPTS` | Yes | Scheduler retry ceiling |

Prefer a restricted Stripe key with the minimum capabilities required for:

- Checkout Sessions read/write;
- Refunds read/write;
- PaymentIntents read;
- Charges read;
- Balance Transactions read.

The last three support provider fee/net reconciliation.

Never use publishable keys as server secrets or expose secret/restricted keys to
templates.

### Reservation, ERP, PDF, and alerts

| Variable | Default | Purpose |
| --- | --- | --- |
| `RESERVATION_ERP_MAX_AUTOMATIC_ATTEMPTS` | `3` | ERP automatic retry ceiling |
| `RESERVATION_PDF_MAX_BYTES` | `15728640` | Private fiscal PDF size limit |
| `LITTER_ALERT_MAX_AUTOMATIC_ATTEMPTS` | `5` | Birth-email retry ceiling |

Dog-specific `reservation_offer_hours` is stored in the database and defaults
to 72 hours, constrained to 1 through 168.

### TOConline

| Variable | Required when enabled | Purpose |
| --- | --- | --- |
| `TOCONLINE_ENABLED` | Always | Explicit integration switch |
| `TOCONLINE_BASE_URL` | Usually default | API host without an added `/api` suffix |
| `TOCONLINE_OAUTH_BASE_URL` | Usually default | OAuth host |
| `TOCONLINE_OAUTH_CLIENT_ID` | Yes | OAuth client |
| `TOCONLINE_OAUTH_CLIENT_SECRET` | Yes | OAuth secret |
| `TOCONLINE_OAUTH_REDIRECT_URI` | Yes | URI registered with TOConline |
| `TOCONLINE_ALLOWED_DOWNLOAD_HOSTS` | Usually default | Trusted fiscal PDF host suffixes |
| `TOCONLINE_TIMEOUT` | Usually default | API/download timeout |
| `TOCONLINE_PAYMENT_MECHANISM` | No | Optional company-specific document field |
| `TOCONLINE_TAX_CODE` | No | Optional company-specific tax field |
| `TOCONLINE_TAX_PERCENTAGE` | No | Optional company-specific tax percentage |

When disabled, no credentials or document overrides are required. Keep the host
allow-list and timeout defaults unless the actual provider contract changes.

Leave optional document overrides empty unless the configured TOConline company
requires verified values. Incorrect tax configuration is more dangerous than
omitting an optional provider field.

### Local chat model

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHAT_MODEL_DIR` | `.models` | Private persistent GGUF directory |
| `CHAT_MODEL_AUTO_DOWNLOAD` | `true` | Permit background download from approved Hugging Face records |
| `CHAT_MODEL_DOWNLOAD_TIMEOUT` | `60` | Read timeout |
| `CHAT_MODEL_MAX_DOWNLOAD_BYTES` | `1350000000` | Download/declared size ceiling |
| `CHAT_CONTEXT_SIZE` | `2048` | llama.cpp context |
| `CHAT_MAX_OUTPUT_TOKENS` | `192` | Generated output ceiling |
| `CHAT_THREADS` | `2` | CPU threads used by llama.cpp |

The following are code-level safety settings rather than environment variables:

| Setting | Value |
| --- | --- |
| `CHAT_BATCH_SIZE` | `128` |
| `CHAT_MODEL_WAIT_SECONDS` | `30` |
| `CHAT_MAX_INPUT_CHARS` | `500` |
| `CHAT_MAX_RESPONSE_CHARS` | `2000` |
| `CHAT_MAX_HISTORY_MESSAGES` | `10` |
| `CHAT_KNOWLEDGE_MAX_CHARS` | `4000` |
| `CHAT_MAX_FAQS` | `3` |
| `CHAT_MAX_BLOG_POSTS` | `10` |
| `CHAT_MAX_CERTIFICATIONS` | `10` |
| `CHAT_MAX_KENNEL_ITEMS` | `5` |
| `CHAT_REQUESTS_PER_MINUTE` | `12` |

Changing these code-level bounds requires tests and resource measurement.

### Uploads and remote images

| Variable | Default | Purpose |
| --- | --- | --- |
| `UPLOAD_MAX_FILE_BYTES` | `104857600` | Total staff upload |
| `UPLOAD_MAX_CHUNK_BYTES` | `5242880` | One chunk |
| `UPLOAD_CHUNK_MAX_AGE_SECONDS` | `86400` | Stale temporary cleanup |
| `EDITOR_IMAGE_MAX_BYTES` | `10485760` | Editor image bytes |
| `EDITOR_IMAGE_MAX_PIXELS` | `25000000` | Decompression-bomb bound |
| `EDITOR_REMOTE_READ_TIMEOUT` | `15` | Remote read timeout |
| `EDITOR_REMOTE_MAX_REDIRECTS` | `3` | SSRF-safe redirect ceiling |

Lower limits are preferable unless real media requirements justify larger
values.

### Contact, translation, and social integrations

| Variable | Purpose |
| --- | --- |
| `RECAPTCHA_SITE_KEY` | Public contact-form reCAPTCHA key |
| `RECAPTCHA_SECRET_KEY` | Server reCAPTCHA secret |
| `DEEPL_AUTH_KEY` | Optional translation helper credential |
| `FACEBOOK_GRAPH_VERSION` | Graph API version |
| `FACEBOOK_PAGE_ID` | Facebook publishing target |
| `FACEBOOK_ACCESS_TOKEN` | Facebook publishing credential |
| `INSTAGRAM_ACCOUNT_ID` | Instagram publishing target |

Social credentials are needed only for the explicit admin publish action.
Translation credentials are not part of normal page rendering.

## Stripe setup

### Local webhook testing

Run Django on port 8000, then:

```bash
stripe listen \
  --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired,refund.created,refund.updated \
  --forward-to http://localhost:8000/webhooks/stripe/
```

Copy the printed `whsec_...` value into local
`STRIPE_WEBHOOK_SECRET`.

The Stripe CLI listener secret may change when the listener restarts. A
production Dashboard endpoint secret normally remains until the endpoint is
recreated or the secret is rolled.

### Production webhook

Create exactly this HTTPS endpoint:

```text
https://<production-host>/webhooks/stripe/
```

Subscribe only to the required events. Store its live signing secret separately
from test and CLI secrets.

### End-to-end test

Use low-value test-mode records and verify:

1. local hold exists before redirect;
2. second user is blocked;
3. completed webhook confirms;
4. dashboard reflects the stage;
5. retry reuses one case;
6. async failure/expiry releases according to stage;
7. refund is idempotent;
8. provider fee/net eventually appears;
9. no card data appears in application logs or database.

## TOConline setup

1. Register OAuth client and exact redirect URI.
2. Configure base/OAuth URLs for the intended TOConline environment.
3. Validate company tax and payment-mechanism defaults.
4. Start with `TOCONLINE_ENABLED=false`.
5. Complete a test Stripe/offline payment and verify a deferred local job.
6. Enable TOConline.
7. Run the scheduler and verify integration by external reference.
8. Verify fiscal number, amount, currency, tax, document number, and PDF.
9. Process a test refund and verify a distinct credit note.
10. Test provider timeout after create and confirm reconciliation prevents
    duplicates.

Never delete a local ERP job to force another create.

## Scheduler

### Command

```bash
poetry run python manage.py process_reservation_workflows --limit 100
```

Run once per minute. Compose starts one looping `reservation_scheduler`
container.

### Operational expectations

- only one scheduler instance is necessary;
- a command crash must be restarted by the process manager;
- one item failure does not abort the batch;
- processing leases are reclaimed after ten minutes;
- retry ceilings lead to visible failure/attention states;
- monitor both command exit/failure logs and business-alert email.

### Manual invocation

It is safe to run the command once during diagnosis. Do not start many
concurrent loops to “make it faster”; database and provider idempotency protects
correctness, but extra workers increase contention and external requests.

## Chat model operation

### Initial catalogue

```bash
poetry run python manage.py loaddata chat_models
```

Then use `/<language>/admin/chat/model-status/` to select and prepare a model.

### Production process count

One WSGI process is required on the constrained 2 GB deployment when the local
model is enabled. Each process owns a separate native model instance.

The provided Docker image therefore runs one Gunicorn `gthread` worker with four
ordinary request threads. Model inference remains serialized by its own lock.
Keep one worker unless production measurement proves that the host has enough
memory for another complete model instance.

WSGI/ASGI starts model preparation when the web process boots. Once ready, the
model remains in memory until that process exits or staff changes the active
model. Configure the hosting platform with one minimum/always-on instance and
disable scale-to-zero or idle suspension. No Python object can remain active
after the platform stops its process.

### Storage

Mount `CHAT_MODEL_DIR` on a private persistent volume. The web process needs
write access. The scheduler does not need to load the model.

If the volume is absent, container replacement loses downloaded models and boot
warm-up starts a new download. With a persistent volume, restart warm-up only
verifies and loads the existing file.

## Containers

### Build and start

```bash
docker compose build
docker compose run --rm web python manage.py migrate
docker compose up -d
```

The multi-stage image:

1. builds Tailwind and copies Leaflet assets;
2. installs Poetry dependencies into a virtual environment;
3. uses a slim Python runtime;
4. runs as non-root `app`;
5. collects static files;
6. exposes port 8000;
7. includes a TCP health check.

Compose provides MariaDB, web, one scheduler, persistent media, and a private
chat-model volume. Before production use:

- replace every default secret;
- use explicit hosts and HTTPS;
- use managed/persistent database storage and backups;
- make media persistent and backed up;
- ensure the Gunicorn process count matches the model memory rule;
- add a reverse proxy/load balancer with request-size and timeout policy;
- route application and scheduler logs to retained storage.

## Database operations

### Migrations

Before deployment:

```bash
poetry run python manage.py makemigrations --check
poetry run python manage.py migrate --plan
```

During deployment:

```bash
poetry run python manage.py migrate --noinput
```

Never edit an already applied migration to change current behaviour. Add a new
migration.

Commercial schema migrations require:

- a rollback/forward-fix plan;
- constraint validation against current data;
- attention to lock duration;
- tests from historical states;
- backup before production application.

### Backups

Back up together:

- relational database;
- media storage.

Optionally back up:

- GGUF model directory to avoid re-download;
- deployment environment in a secrets manager;
- provider configuration documentation.

Do not back up generated static files as primary data; rebuild them from source.

### Restore test

At a regular interval:

1. restore database into an isolated environment;
2. restore media with the same relative paths;
3. run migrations;
4. run `manage.py check`;
5. verify representative public media;
6. inspect commercial snapshots and document ownership;
7. run the chat search-index rebuild;
8. keep Stripe/TOConline/email disabled or pointed to test systems;
9. record restore duration and gaps.

## Static and media

### Source versus generated

| Path | Type |
| --- | --- |
| `styles.css` | Tailwind source |
| `assets/` | Source/public assets |
| `assets/css/styles.css` | Generated Tailwind output committed for deployment |
| `static/` | `collectstatic` output; disposable |
| `media/` | User/admin uploads; durable |

Do not diagnose production CSS against stale `static/` without rebuilding.

### Media security

Development serves media through Django. Production should serve private/public
media according to policy:

- ordinary catalogue media may be served by a controlled media origin;
- fiscal PDFs are stored in the database and downloaded through ownership
  checks, not exposed as public media;
- `.models` and temporary upload chunks must never be under a public static or
  media URL.

## Health checks

```text
GET /health/live/
GET /health/ready/
```

Expected response:

```json
{"status": "ok"}
```

Readiness returns 503 with `{"status":"unavailable"}` when the primary database
query fails.

Use:

- liveness to restart a stuck/dead process;
- readiness to remove an instance from traffic during database outage.

Do not add Stripe, SMTP, TOConline, or model readiness to the core endpoint;
their outage should not take catalogue pages offline.

Model availability is monitored separately through the staff model-status page
and `chat_model_warmup`, `chat_model_ready`, and
`chat_model_preparation_failed` logs. The public site remains ready while the
optional fallback model is loading or unavailable.

## Logging and monitoring

Collect:

- web access logs;
- Django warnings/errors;
- scheduler stdout/stderr;
- structured chat events;
- container restart/health status;
- database capacity and errors;
- HTTP latency/error rate;
- email delivery failures;
- Stripe webhook status in Stripe Dashboard;
- counts by `Payment`, `PaymentRefund`, `ERPDocument`, and
  `LitterBirthNotification` state.

Suggested operational alerts:

| Condition | Severity |
| --- | --- |
| Readiness failing | Critical |
| Stripe webhook failures/signature errors | Critical for online sales |
| Paid charge without durable ERP job | Critical accounting invariant |
| ERP `needs_attention` | High |
| Refund retries exhausted | High |
| Reservation scheduler not running | High |
| Stale processing lease repeatedly reclaimed | High |
| Email authentication/delivery failure | Medium/high |
| Fiscal PDF failed | Medium |
| Chat model unavailable | Low unless chat is a business SLA |
| Litter notifications retries exhausted | Medium |

Do not log secrets, passwords, raw card data, full webhook bodies, full chat
messages, or private fiscal PDFs.

## Incident runbooks

### Web site unavailable

1. Check container/process state and liveness.
2. Check readiness and database.
3. Inspect latest deploy/migration logs.
4. Check disk/memory pressure.
5. Roll forward or back using a known image, without reverting database
   migrations destructively.
6. Keep scheduler stopped only if it is causing provider/database pressure.

### Database unavailable

1. Remove web instance from traffic through readiness.
2. Check database health, connection limits, disk, locks, and credentials.
3. Avoid repeated schema commands.
4. Restore service.
5. Verify commercial transactions during the outage against Stripe events.
6. Run scheduler for reconciliation.

### Stripe checkout cannot initialize

1. Inspect safe application error and `Payment.last_error`.
2. Verify secret key mode and permissions.
3. Verify amount/currency and Checkout expiry policy.
4. Check Stripe service/dashboard.
5. Inspect whether a Session exists by local metadata before retry.
6. Confirm initial pre-reservation was released if setup failed.
7. Retry through the existing dashboard case.

### Webhook stopped

1. Check production endpoint URL and TLS.
2. Check endpoint signing secret and Stripe mode.
3. Inspect Stripe delivery attempts.
4. Repair endpoint/secret.
5. Replay failed events from Stripe.
6. Run scheduler to reconcile expired/stale local payments.
7. Verify event IDs prevent duplicate effects.

### ERP outage

1. Do not alter paid states.
2. Inspect ERP document status/attempts and provider availability.
3. Correct credentials/configuration.
4. For retryable failures, allow scheduler or confirmed admin retry.
5. For uncertain create, search by external reference first.
6. Verify document and PDF separately.

### Email outage

1. Verify SMTP host, port, TLS/SSL, credentials, sender policy, and DNS.
2. Check whether account activation users remain inactive.
3. Use activation resend after repair.
4. Retry fiscal-document delivery through admin.
5. Retry birth notifications through scheduler/admin.
6. Commercial state remains authoritative even when ordinary notification
   email cannot be replayed automatically.

### Chat model failure

1. Check model-status page.
2. Check writable/persistent model directory and free disk.
3. Inspect checksum/download/load logs.
4. Retry from admin.
5. Activate another approved model if necessary.
6. Confirm one-process memory rule.
7. Deterministic catalogue and FAQ answers should remain available.

## Production deployment checklist

1. Review diff, migrations, and release documentation.
2. Run full tests and `manage.py check --deploy` against production-like
   settings.
3. Back up database and media.
4. Build immutable image and CSS/static assets.
5. Configure explicit secrets, hosts, HTTPS, email, Stripe, and optional ERP.
6. Apply migrations once.
7. Start web and one scheduler.
8. Verify liveness/readiness.
9. Verify localized public pages and admin login.
10. Verify media and static asset versions.
11. Verify registration activation and password reset.
12. Verify low-value Stripe flow and signed webhook.
13. Verify admin acceptance, reservation payment, cancellation, refund/credit,
    transfer, and final sale as relevant to the release.
14. Verify ERP document/credit note/PDF or deferred state when disabled.
15. Verify litter birth notification.
16. Verify chat deterministic answer, local fallback, and model status.
17. Monitor logs, provider dashboards, and state queues after release.
