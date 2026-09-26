# Rebase upstream 1.0.5 — candidate 1.1.0

## Scope and Git evidence

- Date: 2026-09-23.
- Upstream: `rodrigouroz/ha-zentraly`, `main` at
  `7f1002c0cd6fe61d8d11669b938cf1e78ae79d00`.
- Published fork before this work: `axcoro/ha-zentraly`, `main` at
  `918cf475f02860c12da068525104cc9f383ef1b6`.
- Local integration branch: `codex/upstream-rebase-local`. A real Git rebase
  completed; `git merge-base --is-ancestor upstream/main HEAD` succeeds. The
  current fix branch is `codex/fix-zttin01-polling`.
- Recovery reference: `backup/pre-upstream-rebase-20260923`, with a verified
  bundle retained privately in the operational workspace.
- Upstream PRs [#7](https://github.com/rodrigouroz/ha-zentraly/pull/7) and
  [#8](https://github.com/rodrigouroz/ha-zentraly/pull/8) closed on 2026-09-23.
  Their metadata and descriptions were recorded before closure. Branches remain.
- On 2026-09-26, the validated candidate was merged into the fork's `main`.
  No upstream push, release tag or GitHub release was created.

## Retained contracts

| Area | Candidate behavior |
| --- | --- |
| Type 2/ZTTWF | Upstream LAN discovery/local client; cloud fallback for reads; `getConfig`/`setConfig`; output-based heating activity; RestoreEntity heating target |
| Type 16/ZTTIN01 | `readAttr`/`writeAttr`, cluster 65513; modes, presets, setpoint, lock, advanced drafts and read-only schedule |
| Type 17/boiler | Existing telemetry and documented advanced controls; no schedule editing or off-delay write |
| Routing | Parent command target where present, child fallback, target MAC, child HA identity |
| Sessions | Complete initial session persisted; existing invalid session starts reauth; strict ID/email validation; no password fallback for a saved token |
| Inventory | Required `ioUser` object and `coUbications` list; explicit empty list valid; malformed polling preserves last coordinator data |
| Writes | One send per intended command; actual readback, at most one repeated confirmation read; no automatic write replay |
| Advanced drafts | Same RAM store through reauth/reload/setup retries; isolated per entry; consumed after successful setup; cleared on removal |
| Migration | Config entry VERSION=5, options and registry identities preserved; no historical suffix deletions |
| Surface | Seven platforms and three services; synthetic smoke fixture has 41 entities across types 2/16/17 |

The type-2 turn-on operation may contain two distinct writes, mode and remembered
target, as upstream does. A failed write stops the sequence without replay.

## Offline evidence

Before integration, the untouched upstream tree passed **33 unittest tests**
and compileall. A Ruff 0.16.8 run reported 12 pre-existing findings; a full
`--no-respect-gitignore` run reported 13 because its ignore pattern also hid
`tests/test_api.py` from the normal lint run. Logs are retained privately.

The original 33 cases are retained under `tests_upstream/` with documented
fixture/harness changes for the stricter contracts. `tests/test_upstream_suite.py`
runs them in a separate Python process, avoiding collisions between the original
and fork Home Assistant stubs. Neither harness is a real Home Assistant runtime.

The rebased candidate passed **136 pytest tests and 317 subtests**, including the
subprocess gate for all **33 upstream unittest cases**. Ruff **0.11.2** (the
existing workspace gate), compileall and whitespace checks passed. Test tooling
versions are pinned in `requirements-test.txt`; CI now runs the same full suite.
Ruff 0.16.8 has a broader default ruleset and is not the lint gate for this change.

Astra high completed an independent production review. One additional regression
was reproduced and fixed by a separate agent: a hub not discoverable on LAN could
be read through cloud but could not receive controls. Cloud is now selected only
when discovery fails before opening a WebSocket. Connection, send or confirmation
failures after that point propagate without a repeated write. Real local-client
fakes cover missing hubs, discovery timeouts, stale addresses and post-send failures.

The review also exposed import-order contamination in the test harness. The API
and advanced tests now reuse the existing shared smoke harness; the same alternate
order changed from five failures to **79 tests/228 subtests passed**, without
changing production behavior or assertions.

The rebase-only baseline was `70829c9`. Commit `f2d352b` fixes ZTTIN01 polling:
the semantic fields from cluster 65513 `readAttr` are merged into each periodic
read, preserving the raw diagnostic attributes. No new command or write retry
was added. A regression test covers `/App` at 18 °C versus direct Away at
19 °C, the missing-attribute fallback, and the one-read path.

## Home Assistant acceptance

The candidate at `f2d352b` was deployed to Home Assistant from committed source
using the official `scripts/deploy-zentraly-component.ps1`. Before deployment,
all 21 destination files matched rollback baseline `70829c9`; afterward, all 21
source/destination hashes matched. Core restarted and the integration loaded.

Before the first rebase deployment, the 20 HA source files matched fork baseline
`918cf475`; the candidate had 21 files including upstream `local.py`. The private
backup from that deployment window contains 83 files (49 component files including
bytecode, 34 configuration/registry files). It passed extraction, path/size/SHA-256
verification and restrictive ACL checks. Before the `f2d352b` deployment, HA was
verified against rollback baseline `70829c9`.

Read-only preflight on 2026-09-26 used the candidate API and existing HA session:
one `/App` request, HTTP 200, complete session, valid matching account identity,
and one type-16 plus one type-17 device. No login or device command was sent.
Values and session credentials are excluded from this record.

The pre-deploy SSH probe rejected the unverified host key; no trust records were
changed or bypassed. Core restart and live checks were completed through the
authenticated Home Assistant browser session.

Live acceptance on 2026-09-26 reproduced and fixed the Away polling discrepancy.
With `/App` at 18 °C and `readAttr` at 19 °C, HA retained 19 °C after Refresh
and a 60-second polling interval with telemetry updated. Away was restored to
18 °C with one Apply; direct readback and HA both showed 18 °C after another
interval. Main climate remained Off at 5 °C, and the lock remained open.

The HA log search for `Zentraly` showed no issues after the restart. The account
has no type-2 device, so physical type-2 acceptance is unavailable. The earlier
boiler Apply discrepancy remains unconfirmed and outside this fix; the boiler
was not modified during this deployment.

## Recovery

For a code rollback, deploy `70829c9` from committed source with the official
script, restart/reload Core, and verify hashes and setup. Keep the private
configuration backup and inventory outside Git; restore configuration/registry
files only if needed to recover identity, without overwriting newer unrelated HA
configuration.

The pre-rebase Git reference restores the prior fork tree. Commit `70829c9` is the
verified rollback state for the `f2d352b` component deployment.
