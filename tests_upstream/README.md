# Upstream regression tests

These four modules retain the 33 tests from upstream commit
`7f1002c0cd6fe61d8d11669b938cf1e78ae79d00`. Run them in their own Python process:

```console
python -m unittest discover -s tests_upstream -v
```

Both this suite and `tests/` install Home Assistant stubs in `sys.modules`.
Their modules must not be collected in the same process. The main suite runs
this directory in a subprocess.

The original behavioral assertions remain, with these adaptations:

- The encrypted login metadata expects the retained empty `MobileTrade` fix
  and app version 7.2.0.
- Dependency stubs include the seven platforms, services and config-entry
  coordinator context. Thermostat fixtures identify device type 2 explicitly.
- Local-read fixtures provide a local client. Config responses include every
  required live-state field, matching the stricter type-2 parser.
- Climate writes change a separate fake physical state. Only the confirmation
  read publishes coordinator data, while the original power/restore assertions
  still verify the remembered heating target.
- Password-only startup continues testing upstream's initial login path, and
  its fake now returns the complete session that startup persists.

The untouched baseline suite passed all 33 tests before integration. No test
was removed or skipped to accommodate the rebase.
