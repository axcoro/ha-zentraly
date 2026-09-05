# Changelog

## 1.1.1 (local candidate)

- Integrate upstream 1.0.4 (`834b587`) with ZTTIN01 and boiler support, seven platforms, advanced-setting readback and decoded schedule telemetry from the local 1.1.0 candidate.
- Retain upstream's persistent installation identity in setup and configuration, replacing the fixed Firebase placeholder token. Keep the local mobile API contract version 7.2.0.
- Preserve all nine upstream API regression cases and add setup/configuration identity lifecycle coverage. The complete offline suite passes 47 tests and 42 subtests.
- Live authentication and device control have not been validated for this candidate. The upstream authentication limitation below remains relevant; passing offline tests does not establish cloud acceptance.

## 1.0.4

Based on v1.0.2, this release replaces the fixed Firebase placeholder token with a stable installation device ID, following the fallback observed in the mobile app. Existing Home Assistant entries reuse an ID derived from their entry ID; newly configured entries save their generated ID. Home Assistant client metadata is retained.

This release does not include the unpublished v1.0.3 LAN changes.

### Validation

- All 9 API regression tests pass, including encrypted-header contents and identity reuse after client recreation.
- Python compilation and whitespace checks pass.
- **Known limitation:** a live login test still failed with `CK_UserFBTokens_strUserFBToken_NoHaIn`. This release does not establish that authentication or thermostat control has been restored.
