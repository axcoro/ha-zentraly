# Zentraly Thermostat Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/rodrigouroz/ha-zentraly.svg)](https://github.com/rodrigouroz/ha-zentraly/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Home Assistant custom integration for **Zentraly WiFi Thermostats** (by PEISA/FV Group - Argentina).

## Upgrade to 1.0.5

Install **v1.0.5** through HACS and restart Home Assistant. Keep the existing
Zentraly integration entry: it contains the saved session used for reconnection.
The session and live-state repair previously installed manually is now included
in the release, so installing this version does not overwrite it with 1.0.4 code.
Home Assistant **2026.9.0 or later** is required.

**Authentication limitation:** a new password-only login may still be rejected
by Zentraly (`CK_UserFBTokens_strUserFBToken_NoHaIn`). The verified recovery path
uses a valid session already stored in Home Assistant. If that session expires
or is revoked, Home Assistant requests reauthentication. This release does not
provide a supported automatic way to obtain a new official-app session. Do not
delete an existing entry to troubleshoot a login failure.

## Features

- Control your Zentraly thermostats from Home Assistant
- View current temperature and humidity
- Set target temperature
- Turn heating on/off
- Automatic device discovery
- Works with Google Home and Alexa through Home Assistant

## Supported Devices

- Zentraly WiFi Thermostat (ZTTWF series)

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/rodrigouroz/ha-zentraly`
6. Select category: "Integration"
7. Click "Add"
8. Search for "Zentraly" and install it
9. Restart Home Assistant

### Manual Installation

1. Download the latest release from [GitHub Releases](https://github.com/rodrigouroz/ha-zentraly/releases)
2. Extract and copy the `custom_components/zentraly` folder to your Home Assistant's `custom_components` directory
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

Contributions are welcome! Please open an issue or pull request on GitHub.

## Disclaimer

This is an unofficial integration and is not affiliated with, endorsed by, or connected to Zentraly, PEISA, or FV Group. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Credits

- Reverse engineering and integration development by [@rodrigouroz](https://github.com/rodrigouroz)
- Built with assistance from Claude Code
