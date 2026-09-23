# Phase A — persistent sessions and reauth

Component 1.1.0; config entry version 5. PR base/rollback:
79cd7bd8795bf958bd743f92c50d02ae2f89ff89 (public main).
Branch: codex/upstream-105-a-session. Exact immutable heads and component hashes are
recorded in the PR description and the accompanying corrected-stack artifact.

## Behavior

- Persist the complete session; restore it without password fallback for saved tokens.
- Propagate HTTP 401/403 through setup, polling and readbacks. Entity/service actions
  start reauth directly via config_entry.async_start_reauth and fail with a sanitized
  error; no extra inventory refresh is required and no write is retried.
- Reject missing/malformed ioUser or coUbications. An explicit empty list remains valid.
- Import a replacement official-app session only in the masked local HA form. Check
  server ID against imported ID and normalized server email against saved email.
  A valid positive stored ID must also match; corrupt stored IDs can recover.
- Preserve the same AdvancedDraftStore in RAM across verified reauth, unload/setup
  and failed setup retries. Consume the handoff after successful platform setup;
  clear it on entry removal. Never auto-apply drafts or save them to disk. Restarting
  HA or a subsequent normal manual reload discards drafts as before.

## Preserved

Seven platforms, three services, child identities, parent/fallback routing, target
MAC/endpoint, app 7.2.0 Action envelope, Firebase/RID and type-16/17 contracts.
Public main's existing empty ivstrUserMobileTrade hotfix is retained. No new dependency.

## Offline evidence — 2026-09-22

82 tests / 192 subtests; Ruff, compileall and whitespace checks pass. Regression tests
were observed failing before their fixes. Includes actual integration reauth/unload/setup
callbacks with synthetic HA stubs. Astra high reviewed the five correction changes
without new actionable findings. The later existing MobileTrade hotfix was checked by
its decrypted-header regression and source parity. No account/device calls or deployment.

## Required live identity gate (pending)

With separate authorization, verify /App returns ioData.ioUser.ioDCModel.ivlngUser
and ivstrUserEmail. Record only presence/type/nonblank/match booleans, never raw
identity, session values or response bodies. Fixtures and APK symbols do not establish
the live response shape. If identity is missing or inconsistent, halt acceptance and
collect contract evidence before changing validation; no speculative fallback.

A must be accepted live before promoting B. This PR does not authorize merge, deploy,
restart or device writes. A rollback uses the public base SHA above.
