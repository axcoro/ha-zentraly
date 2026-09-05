# Zentraly Thermostat Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/rodrigouroz/ha-zentraly.svg)](https://github.com/rodrigouroz/ha-zentraly/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Home Assistant custom integration for **Zentraly WiFi Thermostats** (by PEISA/FV Group - Argentina).

## Features

- Control your Zentraly thermostats from Home Assistant
- View current temperature and humidity
- Set target temperature
- Turn heating on/off
- Automatic device discovery
- Works with Google Home and Alexa through Home Assistant
- ZTTIN01 automatic schedule mode, away preset and child lock
- Read-only schedule telemetry, including the current scheduled temperature and next change
- Thermostat and boiler sensors, advanced settings and manual refresh
- English and Spanish translations for the additional entities and services

## Supported Devices

- Zentraly WiFi Thermostat (ZTTWF series)
- Zentraly ZTTIN01 thermostat (`device_type=16`)
- Zentraly ZTBIN01 boiler extension (`device_type=17`, `BOILER_WIFI_ESP_NOW`), associated with a ZTTIN01 thermostat

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
| `hvac_mode` | Current mode (heat/off; also auto for ZTTIN01) |
| `hvac_action` | Current action (heating/idle/off) |

ZTTIN01 thermostats and boiler extensions also expose sensors and binary sensors for telemetry, numbers and selects for advanced settings, and Apply configuration/Refresh buttons. ZTTIN01 thermostats additionally expose a child lock and read-only schedule sensors.

Advanced settings are staged locally until **Apply configuration** is pressed. Display settings are sent together, matching the mobile app. Applied values are confirmed by device readback; unconfirmed changes remain pending and writes are never retried automatically. **Refresh** discards pending drafts before fetching cloud data.

## Services

The standard Home Assistant climate services are supported:

- `climate.set_temperature` - Set target temperature
- `climate.set_hvac_mode` - Set HVAC mode (heat/off; also auto for ZTTIN01)
- `climate.turn_on` - Turn on heating
- `climate.turn_off` - Turn off heating
- `climate.set_preset_mode` - Select the ZTTIN01 none/away preset

Additional Zentraly services:

- `zentraly.refresh_device` - Refresh cloud data and discard pending drafts; omit `device_id` to refresh all loaded Zentraly devices.
- `zentraly.apply_thermostat_advanced_settings` - Apply ZTTIN01 temperature correction, away temperature and display settings, with readback confirmation.
- `zentraly.apply_boiler_settings` - Apply boiler water temperatures, operating options and on-delay, grouped by cluster and confirmed by readback.

Pass the Home Assistant device registry ID as `data.device_id`. The two configuration services apply supplied values immediately, without a subsequent Apply button press. Their optional fields are described in the Home Assistant action editor.

## Troubleshooting

### Authentication Issues

Make sure you're using the same credentials as your Zentraly mobile app. The integration uses the Zentraly cloud API.

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

This integration was created by reverse-engineering the Zentraly mobile app API. It uses the official Zentraly cloud API hosted on Azure.

**API Endpoint**: `https://ztprdrestservicesv2.azurewebsites.net`

The thermostats communicate via Azure IoT Hub and the integration polls the cloud API for status updates.

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.

## Disclaimer

This is an unofficial integration and is not affiliated with, endorsed by, or connected to Zentraly, PEISA, or FV Group. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Credits

- Reverse engineering and integration development by [@rodrigouroz](https://github.com/rodrigouroz)
- Built with assistance from Claude Code
- ZTTIN01 thermostat and ZTBIN01 boiler extension support contributed by [@axcoro](https://github.com/axcoro).
