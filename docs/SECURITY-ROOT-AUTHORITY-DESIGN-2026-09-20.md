# Root daemon authorization boundary — design and release blocker

Status: design only, not implemented or verified. Finding BSA-2026-005 remains High and open.

## Current authority

`borond` accepts connections from the kernel-reported `boron-api` UID, then
dispatches any registered `OP_TABLE` operation. `_actor`, `_role`, and `_ip`
are client-supplied audit labels. They do not authorize anything. A process
running as `boron-api` can therefore call privileged operations directly,
including account lifecycle, update, firewall, backup restore, and system
bootstrap operations, without using the HTTP routes. The API service can
read the auth database; it currently contains plaintext session IDs. The API
also holds the session-cookie signing secret. Rechecking a forwarded role,
or signing a role with an API-held key, would not contain an API compromise.

## Required boundary

1. Make the root daemon the source of RPC authority. Every external RPC must
   carry a credential that the daemon validates against root-owned state. It
   derives the principal, role, account, reseller relationships, session
   revocation, token age, impersonation state, and target ownership itself.
   Client-supplied actor/role labels become ignored or diagnostic only.
2. Store only a one-way hash of session IDs in the database, as is already
   done for bearer tokens. Migrate existing sessions deliberately: expire
   them with a clear notice or rehash all rows before removing plaintext;
   never accept both raw and hashed identifiers indefinitely. Cookie signing
   remains useful at the HTTP layer but is not the daemon's proof. A DB read
   by `boron-api` must not reveal reusable sessions.
3. Move session creation behind root-side proof of the user's password and
   second factor, including login throttling. The current
   `auth.create_session(panel_user_id)` is a session-minting primitive for
   any caller with the API UID. Give login/recovery, token issuance, and
   impersonation explicit, separate policies rather than exemptions from
   the general RPC check. Internal setup/bootstrap operations must use a
   root-only local command or a separate root-owned socket unavailable to
   `boron-api`.
4. Define an explicit, fail-closed policy for every `OP_TABLE` entry: allowed
   role(s), target parameter or object ID, canonical owner resolver, and
   operation-specific restrictions. Resolve owner from the daemon's DB at
   execution time. Global admin operations need a real administrator
   credential, not a caller-provided `role=admin`. A policy CI check must fail
   on unclassified new operations. The current 412-operation inventory is
   the worklist; name prefixes alone are insufficient for authorization.
5. Delayed jobs must retain a root-derived principal identifier and recheck
   authorization, account state, and ownership before their destructive
   phase. Revocation, suspension, ownership reassignment, impersonation end,
   and reseller plan changes must affect queued work. A mutable job row or
   API-writable path cannot stand in for authorization.
6. Keep service/system privileges out of the public API process where
   practical. Independently gate update, firewall, server credentials,
   restore/import, and account termination with the strongest root-side
   checks. Reject missing peer credentials, malformed frames, unknown
   parameters, and unauthenticated calls before entering worker pools.

The API is still the TLS terminator. A fully compromised API process can
observe credentials presented while it is compromised and can replay a real
administrator session it has seen. The proposed boundary prevents it from
minting admin authority merely by reading the DB or forging RPC metadata;
it does not claim to solve active administrator credential theft. Further
containment would require an independently trusted admin authentication
channel or hardware-bound approval.

## Verification before release

- In a disposable full-system host, connect to the socket as `boron-api`
  without a panel credential and attempt every operation; all must fail
  before side effects. Repeat with customer A, customer B, both resellers,
  admin, impersonated admin, disabled/revoked/expired identities, and
  malformed credentials. Protected canaries must remain unchanged.
- Validate create/revoke/login flows, password and TOTP checks, bearer token
  creation, browser sessions, terminal WebSockets, and background jobs end
  to end. Test the exact migration from the installed 1.5.0 database with
  active sessions, plus restart and rollback behavior.
- Compare the generated operation inventory with the policy matrix in CI.
  Every operation must have a reviewed disposition and a negative test for
  its privileged boundary; high-risk operations need deployed checks.

Do not describe an API-side `require_admin` call, Unix UID check, or signed
client metadata as closure of BSA-2026-005. The finding is closed only when
the daemon independently rejects unauthorized root effects in the tests
above and the migration is deployed and verified.
