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

Pluggy documents that bills are mandatory for regulated Open Finance
connections, that transactions gain `billId` after the statement is returned,
and that bill data is refreshed daily. Pluggy also reports missing bill
permission (`CC_005`) as a product-scoped condition. The adapter therefore
treats `/bills` capability/permission denial as no snapshot, transient
transport/server failures as safe fallback, and authentication, throttling,
malformed payloads, or unexpected client failures as their normal typed errors
rather than a successful incremental sync. See [Credit Card Bills](https://docs.pluggy.ai/docs/credit-card-bills),
[Warnings & Status Codes](https://docs.pluggy.ai/docs/warnings-status-codes),
[Open Finance considerations](https://docs.pluggy.ai/docs/considerations-faq),
and [Operational Rate Limits](https://docs.pluggy.ai/docs/rate-limits-of).

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
- `provider_bill_id` records normalized provider membership independently of
  the user-facing `bill_id`;
- `provider_bill_membership_known` distinguishes an authoritative provider null
  from legacy/sparse membership that has not been observed;
- without a manual override, `bill_id` is updated to the provider bill and
  `effective_date` is recalculated from the provider due date;
- an explicit provider-side null membership clears stale automatic linkage,
  while an omitted/sparse membership field preserves existing state;
- an authoritative non-null bill id missing from the current bill snapshot
  clears stale provider and automatic user-facing linkage, but remains
  "unknown" until it can be resolved; persisted historical bills may confirm
  that an existing B→B identity is unchanged, but never resolve a new A→B or
  unknown→B membership, and manual overrides stay untouched;
- when no successful bills snapshot exists because the capability is
  temporarily unavailable, absence is not evidence and existing links remain
  frozen until a later authoritative snapshot;
- repeated syncs are idempotent.

This removes the existing gap where new rows and exact matches receive bill
linkage, while manual fuzzy matches and recurring placeholders do not.

Finance charges supplied as bill metadata use the same ownership boundary.
Provider-owned amount, currency, date, and raw payload may converge, but ignored
rows remain entirely frozen and a manual bill-date override keeps its effective
date and user-facing bill. An explicit empty `financeCharges` list removes only
unchanged automatic rows; omitted metadata is not interpreted as deletion.

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
the service compares each bill returned by the provider with its eligible
posted line-item sum and logs a structured warning for differences greater than
five cents. Synced rows with known provider membership are grouped by
`provider_bill_id`; unknown legacy/sparse membership and manual/imported rows
fall back to their user-facing `bill_id`. This lets a user compensation complete
a statement without a manual cycle override corrupting provider reconciliation,
while an explicit provider null still removes the row from that statement.
Pending authorizations are excluded, and local/manual bills outside the provider
response are not treated as provider mismatches. The data remains visibly
inconsistent and is retried on subsequent snapshots.

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
14. Rounding noise up to five cents and local bills absent from the provider
    response do not emit false mismatch warnings.
15. Provider membership remains auditable after a user moves a transaction to
    another cycle, without changing the user's effective bill.
16. Ignored or manually moved finance charges are not overwritten or deleted;
    an explicit empty list removes the final untouched automatic charge.
17. Manual/imported compensation rows still count toward the local bill total.
18. Bills authentication, rate-limit, malformed-payload, and programming errors
    are never downgraded to a successful incremental sync.
19. A non-null provider bill identifier that is absent from the bill snapshot
    remains unknown rather than becoming an authoritative null.
20. Transient API-key transport/server failures may use the safe incremental
    fallback, while malformed successful payloads and credential rejection stay
    visible as errors.
21. Moving an existing transaction to an unresolved provider bill cannot leave
    its previous provider bill membership or automatic bill assignment stale.
22. A transaction that remains in the same historical provider bill keeps its
    resolved membership after that bill ages out of the current bills feed.
23. A transient or unsupported bills snapshot cannot erase an existing
    provider membership while incremental transaction sync continues.
24. A successful empty bills response still triggers the complete transaction
    snapshot, allowing authoritative null membership to remove stale links,
    without consuming the daily cadence before a bill appears.
25. A partially malformed bills page fails closed; valid sibling rows are not
    accepted as an authoritative partial snapshot that could unlink history.

## Rollout and rollback

Migration `070` adds one nullable per-account cadence timestamp. Migration `071`
adds nullable `transactions.provider_bill_id` plus an authoritative-membership
flag, backfilled only from unoverridden synced rows, with `ON DELETE SET NULL`.
Neither migration performs destructive
cleanup. Deployment is safe for existing installations: unsupported providers
retain incremental sync, while Pluggy credit-card accounts perform a complete
cached transaction read daily and on manual refresh whenever their bills
endpoint succeeds. The next successful complete snapshot converges provider
membership for older overridden rows. Downgrade removes only the new metadata;
all transaction and bill rows remain valid.

The normal test suite remains SQLite-only. Reproducible PostgreSQL proofs for
the `071` upgrade/backfill/downgrade and the per-connection row lock live in
`tests/test_provider_bill_reconciliation_postgres.py` and are enabled with
`SECURO_TEST_POSTGRES_URL`. They create and remove isolated temporary schemas;
no application rows are modified.

## Follow-ups

- Consider persisting reconciliation health if operators need it in the API/UI;
  the initial implementation emits structured server logs.
- Evaluate transaction webhooks as a latency optimization; polling remains the
  correctness mechanism for self-hosted instances.
- Address bill timeline ordering/presentation independently.
