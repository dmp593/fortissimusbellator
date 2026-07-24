# Glossary

## Business terms

### Active dog

A dog with `Animal.active=True`. Active does not mean for sale or available.

### Asking price

The current public `Animal.price_in_euros`, optionally reduced by the dog's
direct `discount_in_euros`. It is not the audited final sale price.

### Available

A dog that passes the relevant inventory or online pre-reservation policy. The
meaning is computed by `reservations/availability.py`, not by `for_sale` alone.

### Birth alert

An email sent when a litter has an actual birth date and baby count. Customers
may subscribe generally by breed or explicitly to one litter.

### Breeder review

The manual assessment after a pre-reservation fee is paid. It has no automatic
deadline.

### Customer credit

Durable value owed to a customer for use in a later charge. It is not a cash
refund and is not new money for ERP invoicing.

### Final sale

The completed purchase represented by a non-voided `AnimalSale`, including its
final price and date.

### Litter

A planned or born group of babies. Litters are informational and support birth
alerts. The site does not sell or reserve litter places.

### Pre-reservation

The first paid application for one individual dog. It holds the dog while
payment/review is active but does not guarantee breeder acceptance or constitute
the later reservation.

### Reservation

The second stage after breeder acceptance, or a direct staff-created stage. It
normally requires a deposit target and becomes Reserved only when confirmed.

### Reservation deposit target

The total value that must be committed when reservation is confirmed. Online it
is normally a percentage of the snapshotted dog asking price.

### Reservation offer

A time-limited invitation from the breeder to accept reservation terms and
settle the remaining deposit. Default validity is 72 hours.

### Retained value

Settled customer value that is neither refunded nor converted to customer
credit when a process closes.

### Sold

Final public lifecycle state. A non-voided `AnimalSale` overrides pre-reserved
and reserved labels.

### Transfer

A staff-only operation that closes one dog's process and creates a linked
process for another dog, with an explicit money split.

## Commercial records

### Animal sale case

The aggregate root connecting one customer, one dog, all commercial stages,
charges, closures, transfers, and final sale.

### Charge

One stage's amount due and financial aggregate. A sale case has at most one
charge per pre-reservation, reservation, and sale stage.

### Charge adjustment

An immutable signed correction, discount, surcharge, or waiver applied to a
charge with a reason.

### Closure

A durable decision explaining why a process stopped and how its available value
was partitioned into refund, credit, and retained value.

### Credit allocation

An immutable application of customer credit to one charge. It may later be
reversed with audit data.

### Payment

One real or complimentary settlement attempt. A charge may have multiple
payments, including failed technical attempts and later offline settlement.

### Payment refund

One explicit refund instruction and provider outcome. Partial refunds create
separate records.

### Stage

One of pre-reservation, reservation, or final sale. Stages have independent
charges, terms, and payment consequences.

### Snapshot

A copied value preserved on a historical record, such as dog name, price,
customer address, terms, or promotion. Later catalogue/profile changes do not
rewrite it.

### Workflow

The complete commercial sequence represented by one sale case and related
records.

## Status terms

### Awaiting payment

A local stage exists and holds inventory while online checkout is being
prepared or paid.

### Awaiting review

The pre-reservation stage is settled and waiting for breeder decision.

### Confirmed reservation

The reservation deposit charge is fully settled. Public label is Reserved.

### Deferred ERP

A durable fiscal job exists, but TOConline integration is disabled.

### Expired checkout

Stripe Checkout reached its session deadline. For initial pre-reservation this
releases the dog. For reservation, the offer's separate deadline governs the
hold.

### Failed payment

A payment attempt did not settle. Its inventory consequence depends on stage:
initial pre-reservation releases; accepted reservation remains held until offer
expiry.

### Needs attention

An ERP state where automatic retry is unsafe or insufficient, commonly because
a remote create may have succeeded without a confirmed ID.

### Partially paid

A charge has positive settlement below its current total.

### Processing lease

A timestamped claim by a worker for a durable job. Stale leases are reclaimed.

### Retryable failure

A provider failure classified as safe for bounded automatic retry.

### Void charge

A charge that no longer requests further settlement because its stage closed.
Existing payment/refund/credit history remains.

## Payment and provider terms

### Checkout Session

Stripe's hosted payment page object. It is a remote attempt, not a local
commercial state.

### Complimentary payment

A zero-value paid record used when no real payment is due, for example a 100%
promotion.

### External reference

A stable local/provider correlation value used to identify an offline payment
or reconcile a remote fiscal document.

### Gross payment

Original successful real-payment amount before refunds.

### Idempotency

The property that repeating the same logical operation does not create a second
payment, refund, or document.

### Initializing payment

The local payment exists, but Stripe Checkout creation/reconciliation has not
completed.

### Manual payment

Money verified and recorded by staff as cash, bank transfer, card terminal, or
other offline provider.

### PaymentIntent

Stripe's underlying payment intent. Its identifier is stored for validation,
fees, and refunds.

### Provider fee

Stripe processing cost retrieved from charge/balance transaction data.

### Provider net

Amount Stripe retained for the business after processing cost.

### Refund target percentage

The desired cumulative refunded percentage of original eligible payment, not
an additional percentage each time.

### Webhook

A signed provider-to-server event. The Stripe webhook is the normal
asynchronous payment/refund confirmation path.

## ERP and documents

### Credit note

A fiscal correction document created for one successful refund.

### ERP

The external accounting system, currently TOConline.

### ERP document

A durable local job and snapshot for one sale document or credit note.

### Fiscal PDF

The private PDF representation downloaded after ERP integration. Its availability
is independent of document integration.

### Integration attempt

An audit record for one automatic, success-page, or staff ERP processing
attempt.

### Reconciliation

Finding and validating a provider object from stable local metadata/reference
rather than creating another after an uncertain response.

### Sale document

Fiscal document for gross real payment received on one charge. Customer-credit
allocation is excluded to avoid invoicing the same money twice.

## Catalogue terms

### Animal

The generic model name for an individual dog. Code uses `Animal`; public copy
usually says dog.

### Animal kind

A database category such as dog. It is not a Python enum per species/breed.

### Breed

A translated catalogue classification belonging to an animal kind and
optionally another parent breed.

### Certification

A code/name/description record that may apply to breeds and be assigned to an
animal.

### Cover

The first ordered image attachment used as the primary image for an animal or
litter.

### For breeding

An editorial flag placing an active animal in the kennel's breeding-dog
catalogue.

### For sale

An editorial flag placing a dog in the sales catalogue. It does not mean no
customer currently holds it.

### Generated dog

An individual `Animal` created by the litter admin action after birth.

### Public dog

An animal allowed by the relevant public queryset. The exact conditions depend
on page/chat use.

## Alert terms

### Announcement

One durable litter-birth event with name, breed, date, and count snapshots.

### General alert preference

A user's default policy: none, all breeds, or selected breeds.

### Litter override

An explicit subscribe/opt-out for one user and litter. It wins over the general
preference.

### Notification

One durable email-delivery job for one announcement and user.

## Chat terms

### Alias

A staff-reviewed alternative name or question used only for chat entity/FAQ
search.

### Canonical term

A search term derived from the current database record, such as an animal name,
translated breed name, certification code, or translated FAQ question.

### Deterministic expert

Python logic that answers a supported intent directly from current data without
calling the local model.

### Entity

A chat-searchable record type: animal, animal kind, litter, breed,
certification, or FAQ.

### Entity resolution

The process of ranking indexed terms, handling ambiguity, and reloading
candidates through public querysets.

### Expert

A focused response strategy in `chat/experts.py`.

### Grounding

Providing bounded published site facts and requiring the model to use only
those facts.

### Knowledge boundary

The deterministic refusal used when no published query-related fact can support
an answer.

### Local model

The selected GGUF loaded in-process by llama.cpp. It is optional and not the
source of catalogue truth.

### Page context

Allow-listed public page identifiers/labels sent by the browser as a resolution
hint and revalidated by the server.

### Search entry

The ContentTypes-backed derived projection holding canonical terms and aliases
for one searchable object.

### Session state

The last unambiguous entity reference stored in browser `sessionStorage` for
follow-up questions.

## Technical terms

### Active record versus historical record

An active record currently affects public/business behaviour. A historical
record remains for audit but does not block inventory.

### Adapter

A module isolating an external API/runtime, such as `stripe_gateway.py`,
`erp.py`, branded email, or the local model.

### ContentTypes

Django's registry used by generic relations and the polymorphic chat search
projection.

### Derived projection

Rebuildable data optimized for reading/search, such as `ChatSearchEntry`.

### Liveness

Whether the Django process can answer. It does not test dependencies.

### Public queryset

An explicit ORM query defining which records one public feature may expose.

### Readiness

Whether the application can reach its primary database and should receive
traffic.

### Row lock

`SELECT FOR UPDATE` serialization used inside transactions to prevent
concurrent allocation of the same dog or value.

### Source of truth

The authoritative model/service for a fact. Other layers render or adapt it but
must not redefine it.
