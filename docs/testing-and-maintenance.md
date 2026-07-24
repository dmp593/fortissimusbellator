# Testing and maintenance

## Quality contract

Every meaningful change should leave:

- the domain invariant explicit;
- service behaviour covered;
- HTTP/admin authorization covered where applicable;
- external providers mocked at their adapter boundary;
- migrations and translations consistent;
- documentation updated;
- no generated or secret files accidentally added.
- no mutable service, provider client, request, or customer state at module
  scope.

Tests validate behaviour, not private implementation details. A commercial bug
fix should first reproduce the invalid transition or amount and then prove the
correct durable records.

## Standard verification

Use the project environment, never the system Python:

```bash
poetry run python manage.py check
poetry run python manage.py makemigrations --check
poetry run python manage.py test
```

If the local `.venv` is already installed:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

Build frontend assets:

```bash
npm run prod
```

Check patch formatting:

```bash
git diff --check
```

Before production, also run:

```bash
poetry run python manage.py check --deploy
poetry run python manage.py collectstatic --noinput
```

`check --deploy` must use production-like security environment values; local
HTTP defaults intentionally trigger warnings.

## Focused test commands

| Area | Command |
| --- | --- |
| Accounts | `python manage.py test accounts` |
| Uploads/attachments | `python manage.py test attachments` |
| Breeding/litters/alerts | `python manage.py test breeding` |
| Chat | `python manage.py test chat` |
| Promotions | `python manage.py test discounts` |
| Frontoffice/security/contact | `python manage.py test frontoffice` |
| Reservations/payments/admin/ERP/email | `python manage.py test reservations` |
| Project health | `python manage.py test fortissimusbellator` |
| Blog | `python manage.py test blog` |
| Quiz | `python manage.py test quiz` |
| Tags | `python manage.py test tags` |

Run the full suite after focused tests. A passing focused module does not detect
cross-application regressions such as chat availability or translated template
breakage.

## Current test map

### Accounts

`AccountLifecycleTests` covers:

- inactive registration;
- profile creation;
- activation email;
- signed activation;
- safe redirect handling;
- activation resend and account enumeration;
- password reset/change templates and email behaviour;
- authenticated profile/alert pages.

### Breeding

`BreedingPageTests` covers public lists/details, availability presentation, and
pre-reservation action rules.

`LitterAnimalGenerationAdminTests` covers actual-birth requirements, remaining
animal count, and inheritance from litter to generated dogs.

`LitterBirthAlertTests` covers general preferences, per-litter overrides,
announcement creation, delivery, retry, and unsubscribe behaviour.

### Reservations

`ReservationRulesTests` covers eligibility, locking, snapshots, terms, stage
transitions, expiry, cancellation, and labels.

`ConcurrentDogPreReservationTests` covers one-winner concurrency on databases
that support the required transaction semantics.

`StripePaymentWorkflowTests` covers local hold, Checkout initialization,
webhook fulfilment/failure/expiry, retries, idempotency, refunds, and
reconciliation.

`StripeGatewayTests` covers provider payloads, metadata, idempotency keys,
amount conversion, and required Stripe calls.

`AdminSaleWorkflowTests` covers staff pre-reservation/direct
reservation/direct sale, manual settlement, adjustments, credits, transfer,
and final sale.

`AdminAndDocumentSecurityTests` covers admin permissions, protected history,
customer ownership, ERP retry/download/resend, and deletion warnings.

`ERPWorkflowTests` covers durable jobs, disabled/deferred mode, retries,
uncertain create reconciliation, credit notes, PDF validation, and failures.

`CommercialEmailTests` covers branded commercial messages, language, links,
customer/internal variants, state-specific wording, and fiscal attachments.

`PreReservationTermsTests` and fixture tests cover publication selection,
immutability, association, and initial terms content.

The shared `ReservationTestMixin` builds consistent users, dogs, terms, and
commercial records. Prefer extending it to duplicating setup.

### Promotions

`PromotionTests` covers:

- code normalization;
- fixed and percentage amounts;
- purchase stage;
- any/breed/dog scope;
- schedule;
- active flag;
- global/per-user usage limits;
- amount cap;
- checkout preview/final validation;
- deletion protection.

### Chat

`AssistantTests`, `MatchingTests`, and `ModelCatalogTests` cover prompt/context,
Unicode matching, fuzzy thresholds, and model-spec validation.

`LocalModelTests` covers download, checksum, atomic publication, state,
concurrency, activation, and errors without downloading a real GGUF.

`MessageViewTests` covers payload bounds, rate limit, context/state sanitation,
CSRF-compatible JSON behaviour, and safe errors.

`ChatSearchIndexTests` covers synchronization, rebuild, aliases, collisions,
ContentTypes, and public reload.

`ChatServiceTests` covers intents, named entities, ambiguity, compound
responses, public catalogue visibility, commercial availability, FAQ/blog/
certification behaviour, context, one-call fallback, and knowledge boundary.

`AliasSuggestionServiceTests` and `AliasSuggestionAdminTests` cover public
context, parsing, validation, anchors, fallback, permissions, no-auto-save, and
error responses.

Widget/model-status/fixture tests cover browser integration, staff model
controls, and seed records.

### Frontoffice and uploads

Security-header tests cover important response headers. Contact tests cover
branded internal email and safe failure. FAQ tests cover required
pre-reservation policy content.

Upload tests cover staff-only access, chunks, filename/input validation, image
limits, SSRF protections, redirects, and EditorJS responses.

### Blog, quiz, and tags

`BlogPageTests` covers:

- published/inactive post visibility;
- pagination bounds;
- partial load-more responses;
- related-post selection;
- hidden detail redirects;
- generic tag rendering.

`QuizWorkflowTests` and `EmptyQuizTests` cover:

- empty quiz configuration;
- invalid/foreign answer IDs;
- missing score configuration;
- deterministic result and tie policy;
- successful result rendering.

`GenericTagTests` covers per-object uniqueness, reuse on different objects, and
generic-relation deletion.

EditorJS block rendering remains separately responsible for sanitizing each
supported block template. Any new block type must add malformed-input and
output-escaping tests.

## External integration testing

### Stripe

Unit/integration tests patch `reservations.stripe_gateway` or the official SDK
boundary. They must assert:

- provider call arguments;
- local database transition;
- idempotency key stability;
- duplicate event behaviour;
- ambiguous response reconciliation;
- safe failure message.

Do not mock the reservation service itself in a payment workflow test.

Run at least one manual Stripe test-mode flow before release because mocks do
not validate account capabilities, enabled payment methods, webhook
configuration, or provider API policy.

### TOConline

Patch the typed `toconline.api` client in automated tests. Test each boundary:

- local job creation;
- payload;
- provider create success;
- find-by-external-reference reconciliation;
- retryable error;
- uncertain create;
- needs-attention outcome;
- PDF host/size/content validation;
- credit note.

Run a provider sandbox/test-company flow before enabling in production.

### Email

Use Django's locmem backend in tests and inspect:

- recipients;
- reply-to;
- subject sanitization;
- text and HTML alternatives;
- translated state wording;
- absolute public links;
- attachments.

Production SMTP authentication, sender reputation, DNS, and rendering require a
manual deployment test.

### Local model

Never require a GGUF in automated tests. Use a fake tokenizer/completion object
at the `LocalModel`/assistant boundary. Test deterministic experts separately
from model fallback.

Manual model acceptance should evaluate:

- memory;
- first-load time;
- response latency;
- European Portuguese;
- every supported language;
- prompt-injection resistance;
- refusal outside published knowledge;
- JSON alias reliability.

## Commercial test design

### Assert all affected records

A transition test should usually assert:

- sale-case status;
- stage status and audit timestamps;
- charge status/amount due;
- payment/refund/credit records;
- availability helper result;
- public badge/action;
- email side effect after commit;
- ERP durable job state.

Checking only an HTTP redirect is insufficient.

### Time-dependent tests

Use an explicit `now`, controlled deadlines, or patched `timezone.now` where
needed. Assert both sides of:

- checkout hold expiry;
- reservation-offer expiry;
- promotion start/end;
- published terms effective time;
- retry backoff;
- stale processing lease.

Do not use real sleeps.

### Money

Use `Decimal` strings:

```python
Decimal("50.00")
```

Test:

- zero;
- one-cent boundary;
- partial settlement;
- rounding;
- discount equal to amount;
- previous refund plus new target percentage;
- credit plus real payment;
- transfer/sale exact partition equations.

Never use binary float for expected money.

### Concurrency

Concurrency tests need a database that implements row locking. SQLite tests
still verify unique constraints and service errors but cannot prove production
serialization.

At minimum, test:

- two initial pre-reservations for one dog;
- checkout retry versus a new customer;
- transfer versus new sale case;
- delete/configuration edit versus workflow creation;
- duplicate Stripe webhook;
- duplicate refund processing;
- stale ERP lease.

## Migrations

### Workflow

1. Change the model.
2. Create a new migration.
3. Read the generated operations.
4. Add a data migration only when current rows require transformation.
5. Test migrating from the previous release, not only a fresh database.
6. Run `makemigrations --check`.
7. Verify constraints against representative production data.
8. Document operational impact.

### Commercial migrations

Preserve:

- historical status values;
- public UUIDs;
- amount/currency snapshots;
- terms associations;
- provider identifiers;
- target/customer snapshots;
- external references;
- retry/audit records.

When removing a legacy status or field before production history exists, remove
it explicitly. Once real history exists, provide a data mapping and compatibility
period.

### Fixture safety

Fixtures are installation samples, not migrations. Never use `loaddata` to
modify live used terms, payments, or reviewed aliases.

## Translations

### Interface messages

Source strings use Django `{% translate %}`, `gettext`, or `gettext_lazy`.

Workflow:

```bash
poetry run python manage.py makemessages -a
poetry run python manage.py compilemessages
```

Review every generated `.po` change. Do not accept machine translation for
contractual/refund wording without human review.

### Database fields

Apps register translatable model fields in `translation.py`. Modeltranslation
adds language columns through migrations.

When adding a translated base field:

1. register it;
2. create/read migration;
3. populate all required languages;
4. verify fallback;
5. update chat canonical terms if searchable;
6. update fixtures/admin/tests.

### Automated translation helper

`auto_translate_messages` can assist `.po` maintenance using configured
translation credentials. Treat output as a draft. Preserve Django placeholders,
HTML, domain terms, currency, and legal meaning.

## Frontend maintenance

### Template rules

- extend the shared base;
- use semantic headings and controls;
- keep actions as buttons/links and statuses as non-interactive badges;
- preserve keyboard focus and labels;
- include CSRF for POST forms;
- never rely on CSS to enforce permission or availability;
- use the shared lifecycle priority;
- supply chat context only for public IDs/names;
- keep translated strings out of hard-coded JavaScript where the template can
  provide them.

### JavaScript rules

- use small page/component modules;
- progressively enhance server-rendered behaviour;
- render untrusted content with `textContent`;
- handle loading/error/disabled state;
- keep client storage bounded;
- do not calculate authoritative money or state;
- avoid adding a frontend framework without a demonstrated requirement.

### CSS/static verification

After changing templates/classes:

```bash
npm run prod
poetry run python manage.py collectstatic --noinput
```

Test:

- mobile and desktop;
- light and dark themes;
- keyboard navigation;
- focus indicators;
- long translated text;
- empty/error/loading states;
- disabled/sold/pre-reserved/reserved images and badges.

## Adding or changing a public page

1. Define URL name and localization requirements.
2. Use a public manager/queryset with explicit visibility.
3. Use `select_related`/`prefetch_related` for template access.
4. Bound pagination and query parameters.
5. Add authentication/HTTP method decorators.
6. Add semantic template and empty/error states.
7. Add chat context only when useful.
8. Add route, visibility, permission, translation, and query-count tests.
9. Update [Public site and administration](site-and-admin.md).

## Adding a commercial transition

1. Write the business preconditions and money equation.
2. Decide which state machine owns the fact.
3. Define whether the dog blocks in the new state.
4. Implement one atomic service with row locks.
5. Add database constraints/indexes where necessary.
6. Define Stripe/provider idempotency.
7. Define closure/refund/credit consequences.
8. Define ERP/document consequence.
9. Define customer/business email.
10. Add admin/customer action and permissions.
11. Add scheduler recovery if external work can fail.
12. Test success, invalid source states, duplicate call, concurrency, external
    failure, and history.
13. Update [Commercial workflows](commercial-workflows.md).

## Adding an admin action

- use a custom confirmation form for consequential operations;
- require a human-readable reason;
- use `admin_site.admin_view`;
- check `has_change_permission`;
- fetch the object through an authorized/locked service;
- show expected amounts and irreversible effects;
- use Django messages for safe outcomes;
- remove dangerous bulk operations where per-record decisions are required;
- add GET, POST, missing-permission, invalid-state, and double-submit tests.

## Dependency maintenance

Before adding a package:

1. confirm standard library/Django cannot solve the problem clearly;
2. confirm active maintenance and Python/Django compatibility;
3. review transitive dependencies and native footprint;
4. evaluate security/advisories;
5. isolate it behind an adapter;
6. add lockfile changes;
7. test container build and 2 GB runtime impact;
8. document configuration and removal path.

For upgrades:

```bash
poetry lock
poetry install
npm install
npm run prod
poetry run python manage.py test
```

Review provider/library major-version migration guides, especially Django,
Stripe, `django-toconline`, modeltranslation, and llama.cpp bindings.

## Release review

### Code

- no duplicated availability/money logic;
- expected exceptions are handled and unexpected ones logged;
- permissions enforced server-side;
- transactions and locks are scoped;
- no secrets or personal data logged;
- new queries are bounded;
- migrations are forward-safe;
- external calls have timeout/idempotency/recovery.

### Behaviour

- active and historical customer journeys work;
- failure releases or retains inventory intentionally;
- labels are exclusive and correctly prioritized;
- refunds/credits/retained value reconcile exactly;
- emails use correct stage wording;
- disabled ERP does not show a false warning;
- chat never advertises held inventory.

### Artifacts

- CSS rebuilt;
- translations compiled;
- `.env.example` updated for configuration changes;
- docs and diagrams updated;
- no model/media/static/temp/secrets accidentally staged;
- Docker image builds as non-root;
- full suite passes.

## Documentation maintenance

The documentation index states ownership. Update:

| Change | Document |
| --- | --- |
| Application boundary or security/integration design | `architecture.md` |
| Model, relation, constraint, or snapshot | `domain-model.md` |
| Route, page, admin action/filter | `site-and-admin.md` |
| State, transition, money, refund, transfer, sale | `commercial-workflows.md` |
| Stripe/TOConline/env/deployment/scheduler | `operations.md` and, if relevant, `pre-reservations.md` |
| Chat file, intent, expert, entity, model, alias | `chat.md` and short `chat/README.md` |
| Test/process/tooling | This document |
| New ambiguous terminology | `glossary.md` |

Validate relative Markdown links before finishing a documentation change.
