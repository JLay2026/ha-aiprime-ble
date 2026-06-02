# HA-AIPrime-BLE

Home Assistant custom integration for **Aqua Illumination Prime HD** aquarium lights — the BLE-only models controlled by the Mobius / MyAI app.

> 🟡 **Status: read-only working — v0.1.0.** Connects to your AI Prime over BLE, reads device metadata (manufacturer / model / serial / hardware + software revision), tracks BLE signal strength, discovers the device's actual channel set, and polls per-channel state every 30 seconds. **Light control (turning on/off, setting brightness, per-channel sliders) is not yet wired** — it lands in the next release. See [Roadmap](#roadmap) below.

## Hardware support

| Device | Status | Notes |
|---|---|---|
| AI Prime 16 Freshwater | 🟢 read-only working | Connect, metadata, RSSI, per-channel state poll. Verified against MOBIUS at `1C:BC:EC:0A:E2:D0`. Writes pending. |
| AI Prime 16HD (saltwater) | 🟡 untested | Likely works for reads — channel set is discovered from the device, so a different channel layout doesn't require code changes. |
| AI Hydra HD | ❓ unknown | Different chip class — needs verification. |
| AI Nero pumps | ❌ out of scope | Use [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32) instead. |

## Architecture

```
AI Prime HD ◄──BLE/FSCI──► ESPHome Bluetooth Proxy ◄──WiFi──► Home Assistant
                            (ESP32-S3 near tank)                 └─ this integration
```

The integration runs **inside Home Assistant** and uses HA's native `bluetooth` integration to reach the light. If your HA host's onboard Bluetooth doesn't reach the tank reliably (RSSI worse than ~-75 dBm), deploy an ESP32 running [ESPHome Bluetooth Proxy](https://esphome.io/projects/bluetooth-proxy/) near the tank — HA auto-discovers the proxy and routes BLE through it transparently. A reference 3D-printed enclosure for the proxy lives in [`hardware/`](hardware/).

The on-wire protocol is **FSCI** (Framing Serial Connectivity Interface, an NXP convention). The codec is a clean-room port of [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32)'s pump-side implementation — confirmed bit-identical against the AI Prime's Qualcomm QCA4020 via a Day 3 validation test. Live BLE round-trip evidence is embedded in `protocol/fsci.py`.

## Roadmap

### Working today (v0.1.0)
- ✅ Bluetooth auto-discovery (advertises as `MOBIUS`)
- ✅ Reliable BLE connection via `bleak-retry-connector`, works through ESPHome Bluetooth Proxy
- ✅ Reconnect with exponential backoff (1s → 30s cap), bleak disconnect callback triggers fresh epoch
- ✅ Device-side channel discovery via `ATTR_CHANNEL_LIST` — no hardcoded channel ID assumptions
- ✅ Per-channel state polled every 30s via `ATTR_LIVE_CHANNEL_STATE`
- ✅ Standard 0x180A Device Information sensors: Manufacturer, Model Number, Serial Number, Hardware Revision, Software Revision
- ✅ BLE Signal Strength sensor (passive — updates even when GATT link is down)
- ✅ Fan Speed sensor
- ✅ Master Light entity (aggregate on/off + brightness state, read-only for now)

### Coming next
- 🔜 **Firmware version sensor** via FSCI `ATTR_FIRMWARE_VERSION` (lights up the existing entity, currently unavailable)
- 🔜 **Light control writes** — `light.turn_on` / `light.turn_off` / `light.toggle` / brightness, and the per-channel number entities will actually drive the fixture
- 🔜 **Channel-name discovery flow** — at first install, walks each LED channel and asks you to identify the color (replaces the placeholder `Channel 0xNN` labels with `Cool White`, `Royal Blue`, etc.)
- 🔮 Scene + schedule introspection (further out)

## Requirements

- Home Assistant `2025.1.0` or newer.
- HACS installed.
- The light's BLE MAC visible in HA's Bluetooth integration (Settings → Devices & Services → Bluetooth → look for `MOBIUS`).
- **Recommended:** ESP32 BT proxy near the tank if your HA host's RSSI to the light is worse than -75 dBm. The host-only path works but is flaky at long range; an ESPHome proxy at ~5 m from the fixture holds -55 to -65 dBm comfortably.

## Installation (via HACS)

1. HACS → ⋮ menu → **Custom repositories**.
2. URL: `https://github.com/JLay2026/ha-aiprime-ble`. Category: **Integration**.
3. Install → restart Home Assistant.
4. The light will auto-discover via Bluetooth (the `local_name: MOBIUS` declaration in `manifest.json` triggers a discovery flow). Accept the flow.
5. Alternative manual path: Settings → Devices & Services → **Add Integration** → **AI Prime BLE** → select the discovered MOBIUS device from the list.

Once installed, an INFO log line confirms the connection:

```
AIPrimeHub 1C:BC:EC:0A:E2:D0: connected; serial=A09F0AE2D0R1CF
  manufacturer=Aqua Illumination  model=...  channels=0x01, 0x10, 0x11, ...
```

The `channels=...` list is what the device itself reports via `ATTR_CHANNEL_LIST`. If you see an unexpected ID (e.g. `0x07`), that's the device telling you it has a channel our default-label map didn't know about — it'll show up as `Channel 0x07` until labels are updated.

## Channels

The Prime 16 Freshwater has **6 LED channels + 1 cooling fan**, identified by the device-reported channel IDs.

| ID (hex) | Heuristic name | Type |
|---|---|---|
| `0x01` | Fan | cooling fan (exposed as a sensor, not a light) |
| `0x10` | Blue | LED |
| `0x11` | Green | LED |
| `0x13` | Deep Red | LED |
| `0x16` | Moonlight\* | LED |
| `0x19` | Warm White | LED |
| `0x1E` | Cool White | LED |

\* **Caveat:** Channel names are a **heuristic** derived from the Mobius app's slider order at the time of writing. They have NOT been empirically verified by toggling each channel and observing which color lights up. The eventual channel-name discovery flow (see [Roadmap](#roadmap)) will pin the real mapping. In particular, the Moonlight mapping is suspect — Moonlight may live on a separate channel ID that isn't in the table above.

Discovery is dynamic: if the device returns a channel ID we don't have a default label for, the integration auto-generates `Channel 0xNN` rather than dropping the channel.

## Values

Internal scale is **0-1000 (per-mille)**. The integration exposes 0-100 (percent) to users and converts internally via helpers in `const.py`.

## Acknowledgments

- [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32) — reverse-engineered AI's FSCI protocol on the pump side. The lighting product turned out to use bit-identical framing, GATT UUIDs, CRC, and message-ID semantics, so the codec was a near-direct lift.
- The AI engineering team for the Mobius app's "Settings Dump" feature, which made the data model trivially accessible.

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Aqua Illumination** in any way. All product names and trademarks are the property of their respective owners.

**USE AT YOUR OWN RISK.** No warranty. By using this integration you accept full responsibility for any consequences to your livestock, equipment, or property.

## License

MIT
