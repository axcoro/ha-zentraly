# Changelog

## 1.1.0

- Add support for ZTTIN01 thermostats and ZTBIN01 boiler extensions while retaining legacy thermostat compatibility.
- Add thermostat and boiler sensors, child lock, advanced settings, and manual refresh controls, with English and Spanish translations.
- Add an explicit Apply configuration action for advanced settings, including grouped display settings and device readback confirmation. Unconfirmed changes remain pending; writes are not retried automatically.
- Expose decoded thermostat schedules, the current scheduled temperature, and the next scheduled change as read-only telemetry.
- Support mobile API 7.2.0 commands and route child-device commands through their parent when available, preserving Home Assistant device identities.
- Correct mobile-trade login metadata and preserve a stable installation identity across reloads and restarts.
- Expose refresh_device, apply_thermostat_advanced_settings, and apply_boiler_settings services for scripts and automations.
- Expand regression coverage for authentication, device routing, advanced settings, schedules, and platform setup.

## 1.0.4

Based on v1.0.2, this release replaces the fixed Firebase placeholder token with a stable installation device ID, following the fallback observed in the mobile app. Existing Home Assistant entries reuse an ID derived from their entry ID; newly configured entries save their generated ID. Home Assistant client metadata is retained.

This release does not include the unpublished v1.0.3 LAN changes.

### Validation

- All 9 API regression tests pass, including encrypted-header contents and identity reuse after client recreation.
- Python compilation and whitespace checks pass.
- **Known limitation:** a live login test still failed with `CK_UserFBTokens_strUserFBToken_NoHaIn`. This release does not establish that authentication or thermostat control has been restored.
