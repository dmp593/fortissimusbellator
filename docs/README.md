# Fortissimus Bellator documentation

This directory is the maintained technical and operational reference for the
Fortissimus Bellator website. It describes the current codebase, not a future
architecture proposal.

## Reading map

| Document | Use it when |
| --- | --- |
| [Architecture](architecture.md) | You need the system boundaries, request flow, technology stack, security model, or integration map. |
| [Domain model](domain-model.md) | You need to understand an application, model, relationship, snapshot, constraint, or ownership rule. |
| [Public site and administration](site-and-admin.md) | You need to find a page, URL, customer journey, admin screen, filter, or staff action. |
| [Commercial workflows](commercial-workflows.md) | You need the complete pre-reservation, reservation, sale, payment, cancellation, refund, credit, transfer, promotion, or ERP state machines. |
| [Pre-reservation operations](pre-reservations.md) | You are configuring or operating Stripe, TOConline, terms, litter alerts, email, or the reconciliation scheduler. |
| [Chat architecture](chat.md) | You need to understand or change the chat pipeline, entities, intents, experts, search index, local model, aliases, browser state, or safety boundaries. |
| [Operations](operations.md) | You need local setup, environment variables, deployment, health checks, backups, static/media handling, or troubleshooting. |
| [Testing and maintenance](testing-and-maintenance.md) | You are changing code, adding a feature, running tests, managing fixtures/translations, or preparing a release. |
| [Glossary](glossary.md) | A business or technical term is ambiguous. |

The module-level [chat README](../chat/README.md) remains the short deployment
entry point for the local model. The detailed implementation reference is
[Chat architecture](chat.md).

## Documentation authority

Documentation is ordered by authority:

1. Database constraints and committed service code define actual runtime
   behaviour.
2. The documents in this directory explain that behaviour and its operational
   intent.
3. Inline comments explain only local implementation decisions.
4. Screenshots, old tickets, fixture examples, and historical migrations are
   evidence of earlier states, not current product rules.

If code and documentation disagree, treat it as a defect. Verify the current
tests and service implementation, then update either the code or the document
in the same change.

## System ownership at a glance

| Concern | Authoritative module |
| --- | --- |
| Public dog and litter data | `breeding` |
| Dog availability and blocking rules | `reservations/availability.py` |
| Commercial state transitions | `reservations/services/` |
| Financial totals and customer credit | `reservations/services/ledger.py` |
| Stripe state and webhook reconciliation | `reservations/services/payment.py` |
| Promotions | `discounts/services.py` |
| Fiscal documents | `reservations/services/erp.py` |
| Litter birth notifications | `breeding/services/litter_alerts.py` |
| Customer identity and profile | Django auth and `accounts` |
| Branded email rendering | `fortissimusbellator/emails.py` |
| Public chat catalogue | `chat/catalog.py` |
| Chat search entities and aliases | `chat/search_registry.py` and `chat/search_index.py` |
| Public templates and browser behaviour | app templates and `assets/` |
| Runtime configuration | `fortissimusbellator/settings.py` and environment variables |

## Documentation maintenance rule

Update the relevant document in the same change whenever any of these change:

- a model state or transition;
- an availability or concurrency rule;
- a staff action or customer-visible page;
- an environment variable;
- an external integration;
- a scheduled process;
- a public or admin URL;
- a chat entity, intent, expert, or knowledge source;
- a deletion, audit, refund, or terms-retention rule.

Do not duplicate a business rule in several documents. Put its complete
definition in the authoritative document and link to it from shorter operating
guides.
