# Changelog

## 1.0.5

- Reuse complete saved account sessions after restart and HACS updates, instead
  of discarding them and attempting a password login rejected by the provider.
- Prompt for reauthentication when a stored session is incomplete or rejected.
  Validate the replacement account and mask session input.
- Read live thermostat configuration, mark failed device reads unavailable, and
  expose the data source instead of presenting a retained cloud snapshot as live.
- Correct Wi-Fi thermostat modes and relay activity. Restore the last heating
  target after switching off/on, including an integration reload while off.
- Include the previously local repair in the published component. Preserve the
  persistent installation identity introduced in 1.0.4.
- Add regression tests and GitHub Actions checks, plus a HACS release procedure.

Password-only authentication remains subject to the provider's rejection. This
release restores installations with a valid saved session; it does not establish
that a new password login works. Home Assistant 2026.9.0 or later is required.

## 1.0.4

Based on v1.0.2, this release replaces the fixed Firebase placeholder token with a stable installation device ID, following the fallback observed in the mobile app. Existing Home Assistant entries reuse an ID derived from their entry ID; newly configured entries save their generated ID. Home Assistant client metadata is retained.

This release does not include the unpublished v1.0.3 LAN changes.

### Validation

- All 9 API regression tests pass, including encrypted-header contents and identity reuse after client recreation.
- Python compilation and whitespace checks pass.
- **Known limitation:** a live login test still failed with `CK_UserFBTokens_strUserFBToken_NoHaIn`. This release does not establish that authentication or thermostat control has been restored.
