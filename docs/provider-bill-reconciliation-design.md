# Provider bill reconciliation

## Problem

Credit-card providers can expose bills and transactions on different timelines.
A transaction may first be pending without a bill identifier, become posted
later, or only appear after a bill closes. Securo currently rewinds incremental
transaction sync by 14 days. That protects recent updates, but an older
provider-side change can remain outside the window indefinitely, leaving the
stored bill total and its local transaction breakdown inconsistent.

The failure is structural rather than an OFX-import problem. OFX exports are
useful regression oracles for a particular account, but production data must
converge from the configured provider sync.

## Goals

- Reconcile provider bills from provider transactions automatically during the
  normal polling sync, without requiring webhooks.
- Keep provider-specific pagination and freshness behavior inside the provider.
- Reuse the existing transaction ingestion, duplicate matching, category rules,
  recurring matching, and bill-linking behavior.
- Preserve manual bill-date overrides and ignored transactions.
- Import the real provider line items; never invent a residual transaction just
  to make a total match.
- Preserve the current incremental path for providers that do not expose a
  bill-reconciliation transaction feed.

## Non-goals

- OFX ingestion at runtime.
- Provider-specific logic in `connection_service`.
- Webhook infrastructure.
- A synthetic "unreconciled bill adjustment" transaction.
- Changes to bill timeline presentation or future-cycle ordering.
- A new planned-transaction flag or dependence on local-only fields such as
  `is_planned`.

## Design

### Optional provider capability

`BankProvider` exposes an optional asynchronous method that returns the
transaction snapshot suitable for reconciling bills for one account. Its
default result is `None`, which means "unsupported". An empty list means the
provider supports the capability and the authoritative snapshot is empty.

The sync service asks for this snapshot only for credit-card accounts whose
bills were fetched successfully. Scheduled sync performs at most one complete
snapshot per UTC day; a user-triggered refresh always requests it. Initial
connection and any credit-card account discovered after the connection's daily
snapshot always import full history. If the provider returns `None`, sync
uses the existing incremental transaction request unchanged. If it returns a
list, that list replaces the incremental request for that account, avoiding two
reads and ensuring every provider transaction can pass through normal
ingestion.

The Pluggy adapter implements the capability by requesting the complete
cursor-paginated `/v2/transactions` collection (`since=None`). Pluggy controls
how that snapshot is obtained; the core service only understands normalized
`TransactionData`.

### Transaction convergence

All rows in the reconciliation snapshot pass through the same ordered matching
pipeline:

1. exact `(account_id, external_id)` match;
2. manual fuzzy match;
3. pending-to-posted fingerprint match;
4. recurring placeholder match;
5. new transaction insertion.

Whenever an incoming transaction references a bill returned in the current
sync, every matching branch applies the same bill association behavior:

- ignored rows remain frozen;
- a manual `effective_bill_date` remains authoritative;
- otherwise `bill_id` is updated to the provider bill and `effective_date` is
  recalculated from the provider due date;
- an explicit provider-side null membership clears stale automatic linkage,
  while an omitted/sparse membership field preserves existing state;
- repeated syncs are idempotent.

This removes the existing gap where new rows and exact matches receive bill
linkage, while manual fuzzy matches and recurring placeholders do not.

### Consistency model

Polling provides eventual convergence:

- bills and their provider totals are refreshed every connection sync;
- providers with the optional capability return the reconciliation snapshot on
  the first successful scheduled reconciliation of each UTC day and on every
  manual refresh;
- a newly discovered credit-card account requests its own complete snapshot
  even when another account on the connection already reconciled that day;
- `accounts.last_bill_reconciliation_at` is updated only for each account whose
  complete snapshot succeeds; an incremental fallback therefore does not
  consume that account's daily reconciliation attempt;
- late-created and late-updated rows are therefore reconsidered even when they
  are older than the ordinary incremental rewind;
- the next scheduled sync retries naturally after a transient bills or
  transaction-feed failure.

Sync obtains a database row lock on the bank connection before reading or
writing provider data. This serializes manual, scheduled, and startup syncs for
the same connection, preventing concurrent select-then-insert races while
allowing unrelated connections to sync independently.

Securo does not fabricate missing spend. After a complete snapshot is ingested,
the service compares each provider total with the eligible posted linked
line-item sum and logs a structured warning for differences greater than one
cent. Pending authorizations are excluded because they are not yet part of the
posted statement. The data remains visibly inconsistent and is retried on
subsequent snapshots.

## Acceptance cases

1. A transaction older than the incremental window but present in the bill
   reconciliation snapshot is imported and linked to its provider bill.
2. A provider without the optional capability still receives the same
   incremental `since` value and behavior as before.
3. A manual transaction claimed by fuzzy matching receives provider bill
   linkage without losing user-entered classification.
4. A recurring placeholder claimed by sync receives provider bill linkage and
   keeps its recurring association.
5. A pending transaction that later posts under a different provider ID gains
   the correct bill linkage.
6. A manual bill-date override is never replaced by provider data.
7. Repeating the same reconciliation snapshot does not create duplicate bills,
   charges, or transactions.
8. Pluggy requests every cursor page and omits `createdAtFrom` for a bill
   reconciliation snapshot.
9. A successful scheduled snapshot runs at most once per UTC day; a failed
   snapshot is retried by the next scheduled sync, and manual refresh bypasses
   that cadence.
10. An explicit null bill membership clears stale automatic linkage, while an
    omitted field does not.
11. A newly discovered credit-card account, or an existing account whose bills
    first appear later, receives a complete snapshot independently of other
    accounts on the same connection.
12. Pending authorizations do not create a false provider-total mismatch.
13. Concurrent sync entry points serialize on the connection row before any
    provider-backed upsert work begins.

## Rollout and rollback

The change adds one nullable per-account timestamp through migration `070` and
performs no destructive cleanup. Deployment is safe for existing installations:
unsupported providers retain incremental sync, while Pluggy credit-card
accounts perform a complete cached transaction read daily and on manual refresh
whenever their bills endpoint succeeds. Downgrade removes only the cadence
timestamp; all imported rows use the existing transaction schema and remain
valid.

## Follow-ups

- Consider persisting reconciliation health if operators need it in the API/UI;
  the initial implementation emits structured server logs.
- Evaluate transaction webhooks as a latency optimization; polling remains the
  correctness mechanism for self-hosted instances.
- Address bill timeline ordering/presentation independently.
