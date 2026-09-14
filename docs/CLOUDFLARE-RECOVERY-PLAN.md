# Cloudflare-native DNS recovery

This completes the Cloudflare gap in the existing encrypted configuration backup
workflow. Local PowerDNS and email routing recovery are already deployed. This
plan does not replace the full product goal or the subsequent expansion backlog.

## Provider behavior verified 2026-09-14

Cloudflare's batch endpoint executes deletes, patches, puts and posts in that
order, in a database transaction. DNS propagation across its distributed store
is not atomic. Free zones permit 200 operations per batch; higher plans permit
3,500. Use batches of at most 200 without assuming a plan entitlement.

Sources: [Batch record changes](https://developers.cloudflare.com/dns/manage-dns-records/how-to/batch-record-changes/)
and [Batch API](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/batch/).

## Implementation and verification gates

1. One-attempt transport: do not reuse the general retrying client for batch
   mutations. A timeout or server error can follow a committed request. Keep
   current state ambiguous until reread; retain the encrypted previous copy.
   Implemented in `cloudflare.apply_record_batch` and connected to the development restore worker.
2. Native record validation: preserve per-record Auto TTL, proxy status, comments,
   tags, supported settings, private routing, priority and structured data. Check
   names remain inside the selected zone and content matches its DNS type. Handle
   provider-managed records explicitly. Reject unsupported data before live writes
   rather than silently omit it. Saved IDs never authorize a current mutation.
3. Resolve live account/zone bindings and token context from current ownership,
   using the existing snapshot binding checks. Compare current state again after
   the previous native configuration is encrypted, before the first write.
4. Plan mutations from current record IDs and semantic desired records. Preserve
   unchanged records; use updates where appropriate. Keep conflicting CNAME/name
   changes ordered within provider transactions. Preflight the complete selected
   set before any mutation. Large zones require bounded batches with durable
   checkpoints and verification between batches, retaining undo for partial work.
5. Integrate provider-specific state comparison/application into configuration
   restore and its existing encrypted undo. Inspect an uncertain batch outcome
   before deciding completion or failure. Never blindly replay create operations.
   Revalidate account/provider bindings before subsequent writes and finalization.
6. Enable availability in the existing per-zone restore catalog and both-theme
   picker only when validation/application can fulfill these guarantees. Explain
   that DNS changes may take time to propagate; API verification is distinct from
   global resolver convergence.
7. Test native round trips, provider/account isolation, proxy/Auto TTL/settings,
   structured records, protected records, ownership changes, CNAME transitions,
   concurrent edits, multi-batch interruption, timeout after server-side commit,
   undo and retention. Use deterministic HTTP fixtures for fault injection. Live
   proof must use an explicitly isolated connected QA zone if one is available;
   never use customer or panel DNS merely to demonstrate a write.

Current status: common native records, protected record partitioning, current-ID
batch execution and encrypted restore/undo are connected in development. Integration
and local DNS regression tests pass. Broader native schema coverage, UI/release
verification and suitable live provider proof remain; these changes are not yet
deployed. No public DNS changes were made during this work.
