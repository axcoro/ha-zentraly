# Changelog

## 1.0.4

Based on v1.0.2, this release replaces the fixed Firebase placeholder token with a stable installation device ID, following the fallback observed in the mobile app. Existing Home Assistant entries reuse an ID derived from their entry ID; newly configured entries save their generated ID. Home Assistant client metadata is retained.

This release does not include the unpublished v1.0.3 LAN changes.

### Validation

- All 9 API regression tests pass, including encrypted-header contents and identity reuse after client recreation.
- Python compilation and whitespace checks pass.
- **Known limitation:** a live login test still failed with `CK_UserFBTokens_strUserFBToken_NoHaIn`. This release does not establish that authentication or thermostat control has been restored.
