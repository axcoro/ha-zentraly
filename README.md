# Zentraly Thermostat Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/rodrigouroz/ha-zentraly.svg)](https://github.com/rodrigouroz/ha-zentraly/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Unofficial Home Assistant integration for Zentraly thermostats and their boiler extension (PEISA/FV Group, Argentina).

Version 1.1.0 adds ZTTIN01 thermostat and boiler-extension support, advanced settings, and schedule telemetry. See [CHANGELOG.md](CHANGELOG.md) for the changes.

## Supported devices

- Legacy Zentraly Wi-Fi thermostats (`device_type=2`)
- ZTTIN01 Wi-Fi thermostats (`device_type=16`)
- Zentraly boiler extension (`device_type=17`)

## Features

- Climate control with current/target temperature, humidity and heating state
- Heat, off and automatic schedule modes; ZTTIN01 away preset
- ZTTIN01 child lock
- Read-only decoded weekly schedule, current scheduled setpoint and next change
- Boiler and thermostat telemetry exposed as semantic sensors
- Draft-based advanced thermostat and boiler settings with an explicit Apply button
- Manual cloud refresh
- English and Spanish translations

Advanced changes are read back before their drafts are cleared. Fields that cannot be confirmed remain pending, and the integration never retries a write automatically.

The per-device Refresh button and `zentraly.refresh_device` service discard pending local advanced-setting drafts before refreshing. They do not write those drafts to the device.

## Platforms

The integration uses these Home Assistant platforms as applicable to each device:

- `climate`
- `sensor`
- `binary_sensor`
- `number`
- `select`
- `lock`
- `button`

## Installation

### HACS

1. Open HACS and select **Integrations**.
2. Add `https://github.com/rodrigouroz/ha-zentraly` as a custom Integration repository.
3. Install **Zentraly Thermostat**.
4. Restart Home Assistant.

### Manual

Copy `custom_components/zentraly` into your Home Assistant `custom_components` directory and restart Home Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration**, search for **Zentraly**, and enter the same email and password used by the mobile app.

The integration uses the Zentraly cloud. Device identities in Home Assistant remain stable even when a child device must route commands through its parent IoT Hub identifier.

The cloud client also keeps a stable installation identity across reloads and restarts. Existing entries derive it from their Home Assistant entry ID; new entries save the identity used during login. Existing entries do not need to be deleted or recreated for this update.

## Services

| Service | Purpose |
| --- | --- |
| `zentraly.refresh_device` | Discard pending local drafts, then refresh one Zentraly device or all loaded devices. |
| `zentraly.apply_thermostat_advanced_settings` | Apply selected advanced ZTTIN01 fields and confirm them by readback. |
| `zentraly.apply_boiler_settings` | Apply selected boiler fields grouped by cluster and confirm each field. |

Standard Home Assistant climate services are also supported.

## Troubleshooting

Enable debug logging when diagnosing setup or polling:

```yaml
logger:
  default: info
  logs:
    custom_components.zentraly: debug
```

If authentication fails after a mobile-app update, include the integration version, Home Assistant version and a sanitized log excerpt in the issue. Never publish credentials, authorization headers, device serials or MAC addresses.

## Development

Run the offline regression suite and syntax check from the repository root:

```bash
python -m pytest -q
python -m compileall custom_components/zentraly
```

## Disclaimer

This project is not affiliated with or endorsed by Zentraly, PEISA or FV Group. Use it at your own risk.

## License

[MIT](LICENSE)

## Credits

- Reverse engineering and original integration development by [@rodrigouroz](https://github.com/rodrigouroz)
- Original project built with assistance from Claude Code
