# Publishing a Zentraly release

The installed code, GitHub tag and HACS download must be the same release.
Do not publish an unverified authentication change as the latest stable version.

1. Start from current `main`, preserve installation identity and stored sessions,
   and increment `custom_components/zentraly/manifest.json`.
2. Run `python -m pip install -r requirements-test.txt`, then
   `python -m unittest discover -s tests -v` and
   `python -m compileall -q custom_components tests`. Review the diff for secrets.
3. Merge after GitHub Actions passes. Tag that exact commit and initially publish
   it as a prerelease, without marking it Latest.
4. Back up the installed component. Select the exact prerelease in HACS, then
   compare SHA-256 hashes of every installed component file with the tagged files.
   Confirm the installed manifest version and HACS installed version agree.
5. Run `ha core check`, restart Home Assistant, and verify the existing entry and
   entities recover without replacing their saved session.
6. Compare entity values with direct `getConfig` device reads. For each thermostat,
   change the target by 0.5 degrees through Home Assistant and read it back from the
   device. Restore the original target only if it still matches the test value;
   preserve any concurrent user change. Verify both entity and device readbacks.
7. Wait at least one 60-second polling interval and confirm both entities remain
   available and their internal `last_reported` advances. Check filtered logs.
8. Promote the same tag to stable/Latest only after those checks pass. Document
   the tested Home Assistant version and any remaining authentication limitations.

Unit tests use synthetic responses and Home Assistant stubs. They do not prove
that the vendor accepts a login or that a physical device applied a command.
LAN transport requires an enabled, reachable device service; cloud validation is
not LAN validation. A rejected or expired session still needs reauthentication.

Never ship local credentials, session exports, backups or live logs in a release.
