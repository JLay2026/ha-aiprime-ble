# HA-AIPrime-BLE

Home Assistant custom integration for **Aqua Illumination Prime HD 16HD** aquarium lights (BLE-only models controlled via the Mobius / MyAI app).

> ⚠️ **Status: scaffold / v0.0.1.** Loads as a HACS custom integration but does not yet expose functional entities. Protocol layer and entity wiring are in active development. See `PROJECT_PLAN.md` and `PROTOCOL_NOTES.md` in the parent project folder.

## Hardware support

| Device | Status | Notes |
|---|---|---|
| AI Prime 16 Freshwater | 🚧 in development | First target |
| AI Prime 16HD (saltwater) | 🚧 untested | Likely works — channel set may differ |
| AI Hydra HD | ❓ unknown | Different chip class — needs verification |
| AI Nero pumps | ❌ out of scope | Use [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32) instead |

## Architecture

```
AI Prime HD ◄──BLE/FSCI──► ESPHome Bluetooth Proxy ◄──WiFi──► Home Assistant
                            (ESP32-S3 near tank)                 └─ this integration
```

The integration runs **inside Home Assistant** and uses HA's native `bluetooth` integration to reach the light. If your HA host's onboard Bluetooth doesn't reach the tank, deploy an ESP32 running [ESPHome Bluetooth Proxy](https://esphome.io/projects/bluetooth-proxy/) near the tank — HA auto-discovers the proxy and routes BLE through it transparently.

## Requirements

- Home Assistant `2025.1.0` or newer.
- HACS installed.
- The light's BLE MAC visible in HA's Bluetooth integration (Settings → Devices & Services → Bluetooth).
- Recommended: ESP32 BT proxy near the tank if your HA host RSSI to the light is worse than −75 dBm.

## Installation (via HACS)

1. HACS → Integrations → ⋮ menu → **Custom repositories**.
2. URL: `https://github.com/JLay2026/ha-aiprime-ble`. Category: **Integration**.
3. Install → restart Home Assistant.
4. Settings → Devices & Services → Add Integration → **AI Prime BLE**.
5. Select the discovered MOBIUS device from the list.

## Channels

The Prime 16 Freshwater has **6 LED channels + 1 cooling fan**. Channel IDs (derived from the Mobius app's Settings Dump):

| ID (hex) | Name | Type |
|---|---|---|
| `0x01` | Fan | cooling fan, not exposed as light |
| `0x10` | TBD | LED |
| `0x11` | TBD | LED |
| `0x13` | TBD | LED |
| `0x16` | TBD | LED |
| `0x19` | TBD | LED |
| `0x1E` | TBD | LED |

Channel-name mapping is empirically derived during first install — the integration's discovery flow walks through each channel and prompts you to confirm its color.

## Values

Internal scale is **0–1000 (per-mille)**. The integration exposes 0–100 (percent) to users and converts internally.

## Acknowledgments

- [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32) — reverse-engineered AI's FSCI protocol on the pump side; framing reused here.
- The AI engineering team for the Mobius app's "Settings Dump" feature, which made the data model trivially accessible.

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Aqua Illumination** in any way. All product names and trademarks are the property of their respective owners.

**USE AT YOUR OWN RISK.** No warranty. By using this integration you accept full responsibility for any consequences to your livestock, equipment, or property.

## License

MIT
