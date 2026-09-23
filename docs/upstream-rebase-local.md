# Rebase upstream 1.0.5 — local candidate 1.1.0

## Scope and Git evidence

- Date: 2026-09-23.
- Upstream: `rodrigouroz/ha-zentraly`, `main` at
  `7f1002c0cd6fe61d8d11669b938cf1e78ae79d00`.
- Published fork before this work: `axcoro/ha-zentraly`, `main` at
  `918cf475f02860c12da068525104cc9f383ef1b6`.
- Local branch: `codex/upstream-rebase-local`. A real Git rebase completed;
  `git merge-base --is-ancestor upstream/main HEAD` succeeds.
- Recovery reference: `backup/pre-upstream-rebase-20260923`, with a verified
  bundle retained privately in the operational workspace.
- Upstream PRs [#7](https://github.com/rodrigouroz/ha-zentraly/pull/7) and
  [#8](https://github.com/rodrigouroz/ha-zentraly/pull/8) closed on 2026-09-23.
  Their metadata and descriptions were recorded before closure. Branches remain.
- No new publication or remote main update is part of this delivery.

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

Final candidate: **135 pytest tests and 315 subtests passed**, including the
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

The rebased history ends at `acf42df`; the user explicitly approved committing
the validated integration corrections and proceeding with local deployment on
2026-09-23. Remote publication remains outside this stage.

## Home Assistant acceptance

Not deployed yet. The previous component is retained until the candidate is
validated, explicitly approved for commit and deployed using the operational
`scripts/deploy-zentraly-component.ps1` from committed source.

The current HA component's 20 source files were checked against the published
fork `918cf475`: all normalized SHA-256 hashes match. The candidate has 21 source
files, including upstream `local.py`. The private pre-deploy backup contains
83 files (49 component files including bytecode, 34 configuration/registry files).
It passed extraction, path/size/SHA-256 verification and restrictive ACL checks;
the selected source files were stable during capture. Recheck configuration drift
before deployment and take a new protected snapshot if needed.

Read-only preflight on 2026-09-23 used the candidate API and the existing HA session:
one `/App` request, HTTP 200, `numStatus=0`, complete session, positive integer ID
and non-empty email present, both matching the saved account. Inventory is valid:
one type-16 device, one type-17 device and no type-2 device. No login or device
command was sent. This confirms the strict identity contract for this account.
Values and session credentials are excluded from this record.

SSH with strict host checking rejected the host key. No trust records were
changed or bypassed. The Home Assistant browser session still requires the user
to sign in before the planned UI checks and restart.

Live acceptance must record setup, polling, platforms/services and new errors;
repeat the `/App` check after deployment; one reversible
setpoint change and one advanced setting each for thermostat and boiler. Each
requires a readable original value, one write, readback and verified restoration.
A missing prerequisite blocks that action. Type-2 offline coverage is not live
acceptance when no type-2 device is available.

The previous user's report that the deployed stack works is useful context; it
does not validate this new candidate. No live control acceptance is claimed yet.

## Recovery

Keep the private verified component/configuration backup and its inventory outside
Git. Restore component source into a committed recovery checkout and use the
same official deployment script, then restart/reload and verify hashes and setup.
Restore configuration/registry files only if needed to recover identity; a code
rollback alone must not overwrite newer unrelated HA configuration.

The pre-rebase Git reference restores the prior fork tree. The private HA backup
is the authority for the exact files deployed immediately before this candidate.
