# Zentraly ZTTIN01 for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/axcoro/ha-zentraly?include_prereleases)](https://github.com/axcoro/ha-zentraly/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An unofficial, independently maintained fork of the Zentraly Home Assistant
integration. It supports the **ZTTIN01-ZN thermostat** and **ZTBIN01-ZN boiler
extension**, as well as the **ZTTWF thermostat family**. See [Zentraly's product
page](https://www.zentraly.com/termostato-inalambrico-wifi) for product details.

This fork and the original integration use the same Home Assistant domain
(`zentraly`), so install one at a time. Keep your existing configuration entry
when switching between them.

## Compatibility

Home Assistant **2026.9.0 or later** is required.

## Supported devices

| Device | Home Assistant support | Notes |
|---|---|---|
| ZTTWF thermostat family | Thermostat controls | No type-2 device was available for live testing. |
| ZTTIN01-ZN thermostat | Thermostat controls, away setting, lock, advanced settings and schedule readout | Schedule editing is not supported. |
| ZTBIN01-ZN boiler extension | Telemetry and supported advanced settings | Off delay is read-only. |

See [GitHub Releases](https://github.com/axcoro/ha-zentraly/releases) for the
validation notes that apply to each published version.

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

Test versions are published as prereleases and promoted to stable after
validation. To install one, enable prereleases for this repository in HACS. See
[HACS switch entities](https://hacs.dev/docs/use/entities/switch/).

### Manual Installation

1. Choose a version from [GitHub Releases](https://github.com/axcoro/ha-zentraly/releases) and download its source archive
2. Copy `custom_components/zentraly` to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "Zentraly"
4. Enter the email and password you use in the Zentraly mobile app
5. Complete the Android authentication profile, then submit **Sign in and save**

The profile form is a candidate feature, verified offline. A live login with a
reconstructed profile still needs validation; it is not yet a guarantee of
successful authentication. The previous successful local control reused an
original header captured from the Android app.

To edit these values later, open this integration's **Reconfigure** menu and
choose **Credentials and Android profile**. The configured account stays fixed.
Opening or cancelling the form makes no login request. Submitting the final
form attempts one login, checks the account and inventory, then saves the
credentials, profile and session together and reloads the existing entry.
A failed check leaves the local configuration unchanged, but a successful login
on Zentraly's server may already have affected another session.

Use the installation ID (Android ID/GUID), Firebase messaging token (FCM), app
version, phone brand/model, Android **API level** (an integer, not the Android
marketing version), language and country from your Android app. These values
can be identified in its mobile authentication payload; the app's phone settings
may show some metadata, but generally do not expose the FCM token or app-specific
Android ID. Obtaining those two values requires a separate local inspection of
your own app; this integration does not capture them. Enter them only in HA,
never in issues, chats or shared logs. User-Agent is an advanced editable field.

HA generates the request encryption, timestamp, random value and counter. For
new entries only, it can generate a persistent installation ID if left empty;
acceptance of a generated ID has **not** been confirmed. It cannot generate or
renew FCM tokens: if that value stops working, obtain a fresh one from the app
and edit the profile. Do not substitute the GUID for an FCM token.

Available passwords, FCM tokens and GUIDs appear as **•••••••• — configured**.
Expand **Change** to show an empty replacement field; leaving it empty retains
the configured value (or one entered earlier in the same flow). The indicator
is only a label: the real secret is never prefilled or sent to the browser.
Missing secrets have an empty required field. Non-secret profile fields show
their saved values. HA stores
these values in its config entry; masking is not encryption, so protect HA and
its backups. Restarts reuse the saved session without a new profile login.

**Import an existing session** remains available in Reconfigure and reauth;
see [Authentication troubleshooting](#authentication-issues).

## Entities Created

For each thermostat, the integration creates a `climate` entity with:

| Attribute | Description |
|-----------|-------------|
| `current_temperature` | Current room temperature |
| `target_temperature` | Target temperature setpoint |
| `current_humidity` | Current humidity level |
| `hvac_mode` | Current mode (heat, auto where supported, or off) |
| `hvac_action` | Current action (heating/idle/off) |

## Services

The standard Home Assistant climate services are supported:

- `climate.set_temperature` - Set target temperature
- `climate.set_hvac_mode` - Set HVAC mode (heat, auto where supported, or off)
- `climate.turn_on` - Turn on heating
- `climate.turn_off` - Turn off heating

The integration also provides:

- `zentraly.refresh_device`: optional `device_id`; refreshes selected devices and
  clears any unsaved advanced-setting drafts.
- `zentraly.apply_thermostat_advanced_settings`: `device_id`, plus optional
  `temperature_offset`, `away_temperature`, `display_always_on`,
  `display_brightness` and `display_type`.
- `zentraly.apply_boiler_settings`: `device_id`, plus optional
  `boiler_h2o_temperature`, `is_h2o_enabled`, `boiler_heating_temperature`,
  `is_comfort_mode`, `on_delay`, `is_forced_on` and `weather_type`.

Apply services clear a draft only after the device confirms the corresponding
setting. Unconfirmed values remain pending in memory and are lost if Home
Assistant restarts or the integration reloads. Writes are never automatically
retried.

## Troubleshooting

### Authentication Issues

Home Assistant reuses a saved session. Password-only sign-in may be rejected for
some accounts. If an expired session does not trigger a reauthentication prompt,
open the Zentraly integration entry menu and choose **Reconfigure** →
**Import an existing session**. The masked
session field accepts a JSON object with `token`, `user_id` (a positive integer),
`firebase_token` and `device_guid` for the same Zentraly account. The account is
validated before the existing entry is updated. The integration cannot obtain a
new session from the official app automatically. Treat this object as a password;
never post it in issues or logs, and do not delete an existing entry to
troubleshoot sign-in.

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

## API

This integration relies on Zentraly's private API, which may change without
notice.

## Contributing

Contributions are welcome through pull requests targeting `main`. See
[RELEASING.md](RELEASING.md) for versioning, validation and release steps.

## Disclaimer

This is an unofficial integration and is not affiliated with Zentraly, PEISA or FV Group. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Credits

- Original integration and reverse engineering by [@rodrigouroz](https://github.com/rodrigouroz); this fork is based on [their project](https://github.com/rodrigouroz/ha-zentraly).
