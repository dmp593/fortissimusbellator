# Pre-reservations and reservations

The `reservations` app owns the two-stage dog purchase workflow, payments,
refunds, accepted terms, and fiscal documents. The `discounts` app owns
pre-reservation and reservation promotions. `breeding` owns dog payment
configuration and litter birth-alert preferences.

This document is the focused deployment and operating guide. For every state,
transition, admin-created scenario, money equation, cancellation/refund/credit
decision, transfer, final sale, Stripe/ERP consistency rule, and incident
diagnostic, use [Commercial workflows](commercial-workflows.md). The complete
documentation map is [the documentation index](README.md).

## Business lifecycle

1. A customer pre-reserves an eligible, individually published dog and accepts
   the current pre-reservation terms.
2. The application creates `PreReservation` and `Payment` rows before requesting
   Stripe Checkout. This hold prevents two customers from starting payment for
   the same dog.
3. A verified Stripe webhook, not the browser redirect, confirms payment. A
   customer-created pre-reservation then waits for breeder review without a
   time limit.
4. Staff explicitly accepts or rejects a customer-created pre-reservation.
   Staff-created pre-reservations are already approved and auto-accept once
   settled. Acceptance creates a time-limited reservation offer; it never
   confirms the reservation itself.
   Its duration comes from `Animal.reservation_offer_hours`, which defaults to
   72 hours and accepts values from 1 through 168 hours (seven days).
5. The customer accepts the current reservation terms and pays the remaining
   reservation deposit. The retained pre-reservation amount is credited against
   the deposit target.
6. Only successful deposit payment changes the public label from
   **Pre-reserved** to **Reserved**.
7. When staff completes a non-voided `AnimalSale`, **Sold** becomes the single
   public lifecycle label and overrides both reservation labels.

Staff can also create the process at its real business stage: pre-reservation,
direct reservation without a synthetic pre-reservation, or final sale. Staff
may record cash, bank transfer, card-terminal, or other verified offline
payments, apply customer credit, or leave an amount for a registered customer
to pay online. A paid or complimentary staff-created pre-reservation is
auto-accepted because staff creation already represents breeder approval. A
staff-created Stripe pre-reservation auto-accepts after the customer accepts
the terms and verified payment succeeds. The customer receives a branded link
to continue the same pending process. The same `AnimalSaleCase` history and
financial controls apply to every origin.

Administrative zero-value outcomes are explicit: staff enters zero, selects
the complimentary settlement method, and records an audit note. Existing
non-zero charges are never silently overwritten; staff uses an immutable
adjustment, verified payment, or customer credit. If a payment or adjustment
settles a pending stage, state synchronisation occurs atomically and requires a
valid terms acceptance snapshot.

A dog must be active, for sale, unsold, enabled for pre-reservation, have a
positive published price, and have no blocking workflow. The default
pre-reservation fee is EUR 50 and the default reservation deposit target is 50%
of the dog's price; both are configurable per dog.

Failed or expired Checkout Sessions release the dog. Retrying payment reuses the
same purchase and payment records, increments the Checkout attempt number, and
revalidates the dog, current terms, price, and promotion. Customer cancellation,
staff cancellation, rejection, and offer expiry also release the dog.

The Checkout hold applies only while the initial payment is pending. Once the
pre-reservation is paid, the hold timestamp is cleared and breeder review has no
automatic expiry. If the accepted reservation offer is not paid by its deadline,
the reservation becomes expired and the pre-reservation becomes
`reservation_offer_expired`. Both records remain available for financial and
customer history, but neither blocks the dog. The customer must start a new
pre-reservation if the dog remains available.

The fee, discount, dog price, deposit percentage, and terms are immutable
snapshots on the purchase. The corresponding dog price and payment settings
including `reservation_offer_hours` cannot change while an active
pre-reservation or reservation exists. A 100% discount creates a complimentary
paid pre-reservation without Stripe or a fiscal sale document.

`Charge` is the financial source of truth for each stage. It aggregates every
Stripe or manual payment, customer-credit allocation, immutable promotion
snapshot, and signed administrative adjustment. A failed Stripe attempt may
therefore remain in the technical history while a later manual payment correctly
settles the same charge. Settled entries are never rewritten.

PostgreSQL is recommended in production. Dog row locks serialize workflow
changes, and a conditional unique constraint provides a second line of defence
against concurrent pre-reservations. SQLite is suitable for development but
does not provide equivalent `SELECT FOR UPDATE` semantics.

## Litters and birth alerts

Litters cannot be pre-reserved or reserved. Signed-in customers can subscribe to
one litter, all breeds, or selected breeds, and can unsubscribe at any time. An
explicit per-litter choice overrides the general preference. When a litter is
marked born with an actual birth date and baby count, one durable announcement
is created and eligible email deliveries are queued for retry by the scheduler.

The litter admin action creates only `babies - existing animals` records and
requires an actual birth date. Generated dogs inherit the litter's offspring
pre-reservation enabled flag, fee, reservation deposit percentage, translated
description, attachments, and tags.

## Cancellation and refunds

Pre-reservations can be cancelled by the customer only before breeder
acceptance. An accepted or confirmed reservation cannot be cancelled by the
customer on the website; the customer must contact the breeder. Only staff can
cancel the reservation.

Cancellation or rejection never creates a refund automatically. The
pre-reservation terms state that the fee is non-refundable by nature, subject to
mandatory law. Staff must explicitly choose:

- no refund, which is the default;
- an additional fixed amount;
- a target cumulative percentage of the original payment; or
- the full remaining refundable amount.

When staff cancels a reservation, the decision covers every real payment and
credit allocation from the pre-reservation and reservation stages.
A target percentage is applied independently to the pre-reservation payment and
the reservation deposit payment. A fixed amount is allocated to the reservation
deposit first and then to the pre-reservation payment. A full refund creates a
refund request for the remaining refundable real payments. Staff can also
convert part or all of the available value into durable customer credit; any
remainder is explicitly retained. The customer receives a
reservation-specific cancellation email and sees the cancellation, refunds,
and credits in the dashboard.

Each decision creates a separate durable `PaymentRefund`. Multiple partial
refunds are supported, but committed and successful refunds can never exceed the
original payment. Stripe idempotency is stable per refund record, so retrying an
ambiguous network result cannot create a second provider refund.

The admin displays Stripe's fee and retained net amount. If cumulative refunds
would exceed the known retained net, staff must explicitly accept the loss. The
same acknowledgement is required when Stripe financial data is unavailable.
Every successful refund creates a separate TOConline credit-note job when ERP
integration is enabled.

Only staff can transfer an active pre-reservation or reservation to another
available dog. The source remains immutable and closes as transferred; a new
target process is created at the same stage. Staff must split the source value
between transferred customer credit, refund, and retained value. The target
difference can be paid offline or by Stripe. Existing valid stage terms are
carried forward; otherwise acceptance remains pending unless staff explicitly
records acceptance outside the website.

## Payment and accounting boundaries

Stripe hosted Checkout is used directly through the official Stripe SDK. No
payment card data reaches this application.

Each settled charge with a non-zero real-payment amount has one sale
`ERPDocument`; customer credit is excluded so transferred value is not invoiced
twice. The document stores an immutable amount and currency snapshot. Each
successful refund has its own credit-note `ERPDocument`. Payment/refund
confirmation and creation of the durable document job are committed together.
Calling TOConline happens afterward.
Therefore:

- a TOConline outage never changes a paid purchase or successful refund;
- `django-toconline` v2 uses the typed `toconline.api` client for sales and
  print-URL operations;
- when `TOCONLINE_ENABLED=false`, document jobs remain `deferred`, the customer
  sees the payment as complete without a false accounting warning, and enabling
  TOConline later makes the jobs eligible for processing;
- before a financial create, the application records that the result may be
  uncertain; a missing response ID is reconciled by stable external reference
  and never retried automatically when a duplicate sale cannot be excluded;
- ERP integration and PDF retrieval have separate states and errors;
- a PDF outage is non-critical and customers or staff can retry later;
- staff can filter payments/refunds without an integrated ERP document, confirm
  a manual retry, retry or download a PDF, and resend the PDF by email.

The customer dashboard preserves target, customer, price, promotion, and status
history even if the public dog is disabled or deleted. Deleting a dog with
pre-reservation history requires a second explicit admin confirmation. Financial
workflow records remain protected from ordinary deletion.

A non-voided `AnimalSale` is the final availability authority and does not
erase the earlier pre-reservation or reservation history. The public dog and
customer cards render only one lifecycle badge in this order: Sold, Reserved,
Pre-reserved. `Animal.price_in_euros` remains the published asking price and is
also required to calculate the online deposit snapshots; it is hidden once the
dog is sold. It is not treated as an audited final sale price for a manual sale.

The animal and litter admin use Django's server-side autocomplete widget for
father and mother, so large catalogues remain searchable and paginated rather
than rendering every dog as an HTML option. Changelists include search, payment
state, grouped workflow filters, and compact status badges.

## Configuration

Set these values in `.env`:

```dotenv
BUSINESS_NOTIFICATION_RECIPIENTS=operations@example.com,accounts@example.com
# Public origin used by emails and background jobs. Do not include a path.
PUBLIC_SITE_URL=https://fortissimusbellator.pt

STRIPE_SECRET_KEY=rk_test_...
# Use the signing secret printed by `stripe listen` below.
STRIPE_WEBHOOK_SECRET=whsec_...
RESERVATION_CHECKOUT_MINUTES=10

TOCONLINE_ENABLED=true
TOCONLINE_OAUTH_CLIENT_ID=...
TOCONLINE_OAUTH_CLIENT_SECRET=...
TOCONLINE_OAUTH_REDIRECT_URI=https://example.com/oauth/callback

# Optional endpoint and download-security overrides. These are the defaults.
# TOCONLINE_BASE_URL=https://api10.toconline.pt
# TOCONLINE_OAUTH_BASE_URL=https://app10.toconline.pt/oauth
# TOCONLINE_ALLOWED_DOWNLOAD_HOSTS=toconline.pt
# TOCONLINE_TIMEOUT=10

# Optional document overrides:
# TOCONLINE_PAYMENT_MECHANISM=TR
# TOCONLINE_TAX_CODE=NOR
# TOCONLINE_TAX_PERCENTAGE=23

RESERVATION_ERP_MAX_AUTOMATIC_ATTEMPTS=3
RESERVATION_REFUND_MAX_AUTOMATIC_ATTEMPTS=5
RESERVATION_PDF_MAX_BYTES=15728640
LITTER_ALERT_MAX_AUTOMATIC_ATTEMPTS=5

SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
TRUST_PROXY_HTTPS=true
```

`BUSINESS_NOTIFICATION_RECIPIENTS` replaces
`RECIPIENT_LIST_ON_CONTACT_US_REQUEST`. The legacy environment variable remains
a temporary fallback so deployments can be migrated without losing alerts.

`PUBLIC_SITE_URL` must be the externally reachable site origin. Set it to
`http://localhost:8000` for local development and to the HTTPS production
origin in deployment. Commercial emails use it for customer dashboard,
checkout, dog, terms, contact, FAQ, profile, and Django admin links generated
by webhook, scheduler, ERP, and administrative tasks.

For local development, `STRIPE_SECRET_KEY` may contain a restricted `rk_test_`
key. It needs Checkout Sessions and Refunds read/write access, plus read access
to PaymentIntents, Charges, and Balance Transactions for payment-fee
reconciliation. Checkout uses Stripe's dynamic payment methods, so configure
accepted methods in the Stripe Dashboard rather than application code. Do not
enable Stripe Tax unless the account has the required active tax registrations.

When TOConline is disabled, no TOConline environment variables are required.
When it is enabled, configure the OAuth client ID, client secret, and the
registered redirect URI. `TOCONLINE_BASE_URL` is the API host without `/api`;
it is not the OAuth host.

The download host allowlist and timeout are active security controls. Keep them
in the application settings, but their environment variables may be omitted
while the defaults shown above are correct. `TOCONLINE_PAYMENT_MECHANISM`,
`TOCONLINE_TAX_CODE`, and `TOCONLINE_TAX_PERCENTAGE` are optional document
overrides: when empty or undefined, they are not sent to TOConline. Only
configure them when the intended TOConline company requires explicit values,
and validate those values before enabling live payments.
Enable `TRUST_PROXY_HTTPS` only when a trusted reverse proxy sets
`X-Forwarded-Proto`; otherwise terminate HTTPS directly in the application
server path.

## Terms

Pre-reservation and reservation terms are separate models managed in Django
admin. A version becomes effective when `published_at` is set; the most recently
published version of each type is used. Every purchase stores a protected
reference to the exact accepted version. Once used, a version is read-only and
cannot be deleted.

For development or fixture-based initialization, load the same initial terms
with:

```bash
poetry run python manage.py loaddata pre_reservation_terms_v3 reservation_terms_v2
```

Load fixtures only during initial setup. Do not overwrite a used version in a
live database because `loaddata` bypasses admin immutability; publish a new
version instead.

## Customer accounts and email

Registration creates an inactive Django user and profile, then sends a
multipart text/HTML activation email. The activation link uses the current
request host and protocol, preserves a safe same-origin `next` destination, and
signs the user in after successful activation. A failed email delivery leaves
the inactive account available for a later resend. The resend response is
deliberately identical for known and unknown addresses to prevent account
enumeration.

Password reset also sends branded text/HTML email using the current request host
and Django's signed, expiring token. Password change requires an authenticated
session, keeps that session valid after the change, and does not send an email
by design. Configure Django's `EMAIL_*` and `DEFAULT_FROM_EMAIL` settings for
production delivery. When HTTPS is terminated by a proxy, configure
`TRUST_PROXY_HTTPS` only for a trusted proxy so activation and reset links use
the correct secure scheme.

Commercial lifecycle, fiscal-document, litter-birth, and contact notifications
are multipart text/HTML messages using a shared Fortissimus Bellator layout.
They include contextual customer or admin actions, public contact details, and
`Reply-To: geral@fortissimusbellator.pt`. Failed Stripe attempts and expired
reservation offers send explicit state notifications; payment-success,
refund, and ERP work remains idempotent and is not inferred from email
delivery.

## Stripe test webhook

For local test mode, run the Django server on port 8000 and use Stripe CLI to
forward only these events to `http://localhost:8000/webhooks/stripe/`:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`
- `refund.created`
- `refund.updated`

The endpoint is intentionally outside translated URL prefixes. It accepts POST
requests only and verifies every request with `STRIPE_WEBHOOK_SECRET`.

For local testing with Stripe CLI:

```bash
stripe listen \
  --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired,refund.created,refund.updated \
  --forward-to http://localhost:8000/webhooks/stripe/
```

Use the signing secret printed by that Stripe CLI process as
`STRIPE_WEBHOOK_SECRET`. A CLI secret is local to the listener and can change
when it is restarted. A production Dashboard webhook secret has no scheduled
expiry, but it changes if the endpoint is recreated or its secret is rolled.

## Scheduled reconciliation

Run one scheduler instance every minute:

```bash
poetry run python manage.py process_reservation_workflows --limit 100
```

The command reconciles expired Checkout Sessions, retries ambiguous/refundable
Stripe work, refreshes provider fee/net data, expires reservation offers,
processes deferred/retryable ERP jobs, reclaims stale leases, retries PDF
downloads, and sends queued litter birth emails. A TOConline create without a
confirmed document ID is excluded from automatic retry and requires an admin
reconciliation decision, preventing duplicate fiscal documents. Keep a single
scheduler instance to avoid unnecessary duplicate work.

Monitor command failures and alerts sent to
`BUSINESS_NOTIFICATION_RECIPIENTS`. A paid reservation whose ERP state is
`needs_attention` requires a staff retry from Django admin after correcting the
configuration or outage.

## Deployment checklist

1. Apply migrations with `python manage.py migrate`.
2. Build CSS and static files with `npm run prod` and
   `python manage.py collectstatic --noinput`.
3. Configure HTTPS, Stripe keys, the signed webhook, and TOConline OAuth.
4. Start the one-minute reconciliation scheduler.
5. Load or publish both terms types and configure at least one priced dog.
6. Test the complete low-value flow: pre-reservation payment, admin acceptance,
   offer expiry, deposit payment, direct offline reservation, transfer with a
   value difference, all public labels, cancellation split into refund/credit/
   retained value, ERP sale and credit note, private PDF downloads, and emails.
7. Mark a test litter born and verify birth-alert delivery and retry state.
8. Test registration activation, activation resend, password reset, and password
   change against the production email backend and public HTTPS host.
