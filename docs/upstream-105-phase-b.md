# Phase B — validated ZTTWF cloud state

> Historical stack record (2026-09-22). The current local candidate is the
> [real upstream rebase](upstream-rebase-local.md), which also preserves upstream
> LAN transport and heating-target restoration. These are old branch references.

Component 1.1.0; config entry version 5. Branch codex/upstream-105-b-zttwf.
Base and rollback: 365e4258f175daa483e8c67f6dd9bef062927001 (public phase A).
Exact immutable head and component hashes are recorded in the PR description and
accompanying corrected-stack artifact. Accept A live before promoting B.

## Behavior

- Read type-2 getConfig over the existing app 7.2.0 Action transport. Validate required
  temperature, target, integer mode and binary output; optional fields remain unknown
  when absent. Reject malformed values and contradictory duplicates.
- Mark only the failing type-2 device unavailable for local read failures; authentication
  still aborts the coordinator cycle. Preserve type-16/17 reads and parent/child routing.
- HEAT writes mode 2, OFF mode 0; any nonzero integer read mode represents heat.
  Activity follows actual output (HEATING/IDLE), without comparing temperatures.
- Round targets to centidegrees: 16.06 sends 1606. Keep inclusive absolute ±0.01 °C
  confirmation tolerance and do not restrict inputs to the UI step.
- Send one write and read once; reread only once on valid-but-different state. Publish
  observed values, never the requested target optimistically. Reauth uses phase A's
  direct action boundary, without a refresh or a repeated write.

## Offline evidence — 2026-09-22

105 tests / 285 subtests; Ruff, compileall and whitespace pass. Covers parser failures,
mode/output semantics, mixed families, siblings, routing, real API wire envelopes,
16.06 rounding, inclusive tolerance boundaries, mismatch and auth readbacks. Existing
seven-platform/three-service smoke and all A regression tests pass cumulatively.
Astra high reviewed the corrections without new actionable findings; tests use HA
stubs. Actual firmware, cloud and Home Assistant acceptance remains pending.

## Promotion checklist (pending)

1. Accept phase A on its immutable SHA, including its read-only server identity gate.
2. With separate deployment authorization, deploy this B SHA using the canonical script.
3. Verify inventory and getConfig reads, HA availability and heat/output semantics.
4. Only with separate authorization, perform one reversible small setpoint change,
   confirm with an actual read, and restore the original value. Never retry a write.
5. Record evidence and deployed SHA. On rejection, return to the accepted A SHA above.

No LAN, RestoreEntity, new dependencies or unvalidated boiler writes are introduced.
This PR does not authorize merge, deploy, restart or real device writes.
