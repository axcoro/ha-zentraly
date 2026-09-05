# Zentraly Thermostat

Home Assistant support for legacy Zentraly Wi-Fi thermostats, ZTTIN01 thermostats and the Zentraly boiler extension.

## Highlights

- Climate, temperature, humidity and heating state
- ZTTIN01 modes, away preset and lock
- Read-only decoded weekly schedule
- Semantic thermostat and boiler telemetry
- Draft-based advanced settings with explicit apply and readback confirmation
- Manual refresh and English/Spanish translations

Advanced fields remain pending when the device does not confirm the requested value. Writes are never retried automatically.

The Refresh button and `zentraly.refresh_device` service discard pending local advanced-setting drafts before refreshing; they do not write those drafts to the device.

After installation, add **Zentraly** from **Settings → Devices & services** and use the credentials from the Zentraly mobile app.
