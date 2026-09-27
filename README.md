# Zentraly ZTTIN01 for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An unofficial, independently maintained Home Assistant fork focused on the
Zentraly **ZTTIN01 thermostat** and its **boiler extension**. It also retains
support for the ZTTWF type-2 thermostat family.

The manufacturer's product page lists the commercial models [ZTTIN01-ZN
thermostat](https://www.zentraly.com/termostato-inalambrico-wifi) and
ZTBIN01-ZN extension, and links the [quick installation guide](https://www.zentraly.com/guiaRapida/termostato_inalambrico_wifi.pdf).
The integration's internal device types come from reverse engineering; the
manufacturer's page does not document those type numbers.

This fork uses the same Home Assistant domain (`zentraly`) as the original
integration. Install it as an alternative to the original; do not install both
at the same time.

## Fork status — 1.1.0

This fork is based on upstream commit `7f1002c` (v1.0.5), with its session
handling and discovery plus this fork's ZTTIN01/type-16 and boiler-extension/
type-17 support. Config entry version remains 5, and existing entries and
entity identifiers are preserved. The 1.1.0 candidate passed offline checks
and live acceptance on the maintainer's installation. No release tag has been
published; HACS installs the repository's default branch. Home Assistant
**2026.9.0 or later** is required.

See [validation and recovery](docs/upstream-rebase-local.md) for the candidate's
validation evidence.

## Supported devices

| Device | Implemented support | Verification and limits |
|---|---|---|
| ZTTWF family (type 2) | `getConfig`/`setConfig`, HEAT/OFF | Support retained; no type-2 device was available for live verification on the maintainer's installation. |
| ZTTIN01 (type 16) | `readAttr`/`writeAttr`, HEAT/AUTO/OFF, away preset, lock, advanced settings, decoded schedule readout | Offline checks and live readback verification on 2026-09-26. Schedule editing is not supported. |
| Boiler extension (type 17) | Telemetry and documented advanced settings | Offline checks and live readback verification of the heating-water setting on 2026-09-26. Schedule editing and writes to `ivnumDeviceOffDelay` are not supported. |

The integration provides `climate`, `sensor`, `binary_sensor`, `number`,
`select`, `lock` and `button`. Advanced number/select entities edit an
in-memory draft; the corresponding apply button or service sends the changes
and confirms them from device readback.

## Authentication and state

Saved sessions are reused. Initial setup still accepts email/password and saves
the complete session returned by the provider. A new password-only login may
still be rejected by Zentraly (`CK_UserFBTokens_strUserFBToken_NoHaIn`). If a
saved session expires or is revoked, Home Assistant requests reauthentication
through the masked official-app session JSON field. The integration does not
provide a supported automatic way to obtain a new official-app session; do not
delete an existing entry to troubleshoot a login failure.

Reads can fall back from LAN to cloud. A write selects one transport and is
never automatically replayed through another transport. State changes are
published from device readback, with at most one repeated confirmation read.
Advanced drafts survive reauthentication and setup retries in memory; restarting
Home Assistant or reloading the integration discards them.

## Features

- Control your Zentraly thermostats from Home Assistant
- View current temperature and humidity
- Set target temperature
- Turn heating on/off
- Automatic device discovery
- Works with Google Home and Alexa through Home Assistant

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/axcoro/ha-zentraly`
6. Select category: "Integration"
7. Click "Add"
8. Search for "Zentraly ZTTIN01" and install it
9. Restart Home Assistant

### Manual Installation

1. Download the [repository source archive](https://github.com/axcoro/ha-zentraly/archive/refs/heads/main.zip)
2. Copy `custom_components/zentraly` to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "Zentraly"
4. Enter your Zentraly app credentials (same email/password you use in the Zentraly mobile app)

## Entities Created

For each thermostat, the integration creates a `climate` entity with:

| Attribute | Description |
|-----------|-------------|
| `current_temperature` | Current room temperature |
| `target_temperature` | Target temperature setpoint |
| `current_humidity` | Current humidity level |
| `hvac_mode` | Current mode (heat/off) |
| `hvac_action` | Current action (heating/idle/off) |

## Services

The standard Home Assistant climate services are supported:

- `climate.set_temperature` - Set target temperature
- `climate.set_hvac_mode` - Set HVAC mode (heat/off)
- `climate.turn_on` - Turn on heating
- `climate.turn_off` - Turn off heating

The integration also provides:

- `zentraly.refresh_device`: optional `device_id`; discards pending drafts for
  the selected devices and refreshes their state.
- `zentraly.apply_thermostat_advanced_settings`: `device_id`, plus optional
  `temperature_offset`, `away_temperature`, `display_always_on`,
  `display_brightness` and `display_type`.
- `zentraly.apply_boiler_settings`: `device_id`, plus optional
  `boiler_h2o_temperature`, `is_h2o_enabled`, `boiler_heating_temperature`,
  `is_comfort_mode`, `on_delay`, `is_forced_on` and `weather_type`.

Apply services preserve unconfirmed draft values. Controls retain the child's
Home Assistant identity while commands use its parent when present.

## Troubleshooting

### Authentication Issues

A saved session is reused automatically. If Home Assistant requests
reauthentication, its masked session field accepts a JSON object with `token`,
`user_id` (a positive integer), `firebase_token`, and `device_guid` for the same
Zentraly account. The account is validated before the existing entry is updated.
Treat this object as a password: never post it in issues or logs.

Password-only login can still be rejected by the private service. A working
official app session does not prove that a new login will succeed.

### Devices Not Showing

- Check that your thermostats are connected to WiFi and showing as online in the Zentraly app
- Try restarting Home Assistant after adding the integration

### Debug Logging

Add this to your `configuration.yaml` to enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.zentraly: debug
```

## Technical Details

This integration uses a private, reverse-engineered Zentraly API hosted on Azure.
It is not a supported public API and may change without notice.

**API Endpoint**: `https://ztprdrestservicesv2.azurewebsites.net`

The integration polls live device configuration through Azure IoT Hub every
60 seconds. It attempts local WebSocket transport only when the account declares
it enabled and the device can be discovered. Otherwise it uses the cloud.
The `data_source` attribute identifies the active transport. Local transport has
not been validated on the ZTTWF01 devices used for the cloud recovery checks.

See [CHANGELOG.md](CHANGELOG.md) and [RELEASING.md](RELEASING.md) for changes and
the required publication checks.

## Contributing

Contributions are welcome. Please open an issue or pull request in
[this repository](https://github.com/axcoro/ha-zentraly).

## Disclaimer

This is an unofficial integration and is not affiliated with, endorsed by, or connected to Zentraly, PEISA, or FV Group. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Credits

- Original integration and reverse engineering by [@rodrigouroz](https://github.com/rodrigouroz); this fork is based on [their project](https://github.com/rodrigouroz/ha-zentraly).
- Built with assistance from Claude Code
