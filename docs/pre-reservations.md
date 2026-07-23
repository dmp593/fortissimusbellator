# Pre-reservations

The `reservations` app owns reservation lifecycle and payment records. The
`discounts` app owns promotion configuration. Dogs and litters only expose the
small set of availability and fee fields that belong to those catalogue models.

## Business rules

- A dog must be active, for sale, unsold, enabled for pre-reservation, and not
  already reserved.
- A litter is eligible only in `born` or `ready` state and after its actual
  `babies` count is known.
- `pre_reservation_capacity` is between zero and the actual babies born. It may
  deliberately be lower than the born count.
- A pending reservation consumes capacity before Stripe Checkout is created.
  It remains blocking until payment succeeds, Stripe reports failure or expiry,
  or cancellation safely closes the Checkout Session.
- Customer cancellation is non-refundable. Staff cancellation schedules a
  Stripe refund and a TOConline credit note after the refund succeeds.
- The fee and promotion are copied to immutable reservation snapshots. Current
  target prices cannot be changed while a capacity-consuming reservation exists.
- A 100% discount confirms a zero-value reservation without Stripe and does not
  create a fiscal receipt.

Target row locks serialize checkout attempts. Conditional database constraints
also prevent more than one active reservation per dog and more than one active
place in the same litter per customer. PostgreSQL is recommended in production
because it provides the row-lock semantics used to protect litter capacity.

## Payment and accounting boundaries

Stripe hosted Checkout is used directly through the official Stripe SDK. No
payment card data reaches this application.

Payment confirmation and creation of the durable pending `ERPDocument` row are
committed in the same database transaction. Calling TOConline happens afterward.
Therefore:

- a TOConline outage never changes a paid reservation back to unpaid;
- `django-toconline` v2 uses the typed `toconline.api` client for sales and
  print-URL operations;
- before a financial create, the application records that the result may be
  uncertain; a missing response ID is reconciled by stable external reference
  and never retried automatically when a duplicate sale cannot be excluded;
- ERP integration and PDF retrieval have separate states and errors;
- a PDF outage is non-critical and customers or staff can retry later;
- staff can filter paid reservations without an integrated ERP sale, retry the
  integration, retry or download a PDF, and resend the PDF by email.

The customer dashboard preserves target, customer, price, promotion, and status
history even if the public dog or litter is disabled or deleted.

## Configuration

Set these values in `.env`:

```dotenv
BUSINESS_NOTIFICATION_RECIPIENTS=operations@example.com,accounts@example.com

STRIPE_SECRET_KEY=rk_test_...
# Use the signing secret printed by `stripe listen` below.
STRIPE_WEBHOOK_SECRET=whsec_...
RESERVATION_CHECKOUT_MINUTES=30

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

RESERVATION_ERP_MAX_AUTOMATIC_ATTEMPTS=5
RESERVATION_PDF_MAX_BYTES=15728640

SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
TRUST_PROXY_HTTPS=true
```

`BUSINESS_NOTIFICATION_RECIPIENTS` replaces
`RECIPIENT_LIST_ON_CONTACT_US_REQUEST`. The legacy environment variable remains
a temporary fallback so deployments can be migrated without losing alerts.

For local development, `STRIPE_SECRET_KEY` may contain a restricted `rk_test_` key. Grant it only
Checkout Sessions and Refunds read/write permissions. Checkout uses Stripe's
dynamic payment methods, so configure the methods you accept in the Stripe
Dashboard rather than restricting them in application code. Do not enable
Stripe Tax unless the account has the required active tax registrations.

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

## Pre-reservation terms

Terms are managed in Django admin. A version becomes effective when
`published_at` is set; the most recently published version is shown in the
checkout and on the public terms page. Each reservation stores a protected
reference to the exact version accepted by the customer. Once a version has
been accepted, the admin can view it but cannot change or delete it.

For development or fixture-based initialization, load the same initial terms
with:

```bash
poetry run python manage.py loaddata pre_reservation_terms_v1
```

Load this fixture only during initial setup, before customers accept version
v1. Do not reload it in a live database with existing reservations because
`loaddata` bypasses the admin immutability rule. Publish a new terms version
instead.

## Stripe test webhook

For local test mode, run the Django server on port 8000 and use Stripe CLI to
forward only these events to `http://localhost:8000/webhooks/stripe/`:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`

The endpoint is intentionally outside translated URL prefixes. It accepts POST
requests only and verifies every request with `STRIPE_WEBHOOK_SECRET`.

For local testing with Stripe CLI:

```bash
stripe listen \
  --events checkout.session.completed,checkout.session.async_payment_succeeded,checkout.session.async_payment_failed,checkout.session.expired \
  --forward-to http://localhost:8000/webhooks/stripe/
```

Use the signing secret printed by the CLI as `STRIPE_WEBHOOK_SECRET`.

## Scheduled reconciliation

Run one scheduler instance every minute:

```bash
poetry run python manage.py process_reservation_workflows --limit 100
```

The command reconciles Checkout Sessions whose local hold window passed,
retries Stripe refunds, claims pending/retryable ERP jobs, reclaims stale ERP
leases, and retries failed PDF downloads. A TOConline create without a confirmed
document ID is deliberately excluded from automatic retry and requires admin
reconciliation, preventing duplicate fiscal documents. Keep a single scheduler
instance to avoid unnecessary duplicate PDF downloads.

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
5. Make a low-value test reservation and verify the Stripe payment, ERP receipt,
   private PDF download, emails, admin status, cancellation, and refund path.
