# HA-AIPrime-BLE

Home Assistant custom integration for **Aqua Illumination Prime HD** aquarium
lights — the BLE-only models that normally can *only* be controlled from the
Mobius / myAI phone app. No official API, no cloud, no local control... until now.

> 🟢 **Status: full local control — v0.3.0.** Turn the light on/off, set
> brightness, drive each color channel, **deploy app-authored `.aip` schedule
> profiles to the light's own on-device scheduler** (so it runs autonomously
> even if Home Assistant or the Bluetooth proxy goes down), **upload new `.aip`
> profiles straight from HA**, and read full device status — all over local BLE.

This started as a read-only metadata reader. Getting to full control meant
**reverse-engineering Aqua Illumination's proprietary FSCI-over-BLE protocol**:
the live all-channel control write, the native schedule format, and the
per-channel color map — decoded from real myAI app traffic and verified
**byte-for-byte** against it.

---

## What you get

### Live control
- **On / off + brightness** (`light.ai_prime_16hd_freshwater`).
- **Per-color channel sliders** (0–100%) for all 6 LED channels.
- Controls drive the fixture instantly over BLE via the decoded
  attribute-407 bulk-write path (all 7 channels in one frame).

### Native schedule deploy (the headline feature)
- Import an app-authored **`.aip`** profile (exported from myAI) and **deploy
  it to the light's own internal scheduler** — the device then runs the daily
  ramp on its own. **Outage-safe:** lighting keeps following the schedule even
  if HA, Wi-Fi, or the BLE proxy is offline.
- A **profile selector**, a **Deploy** button, and an **active-profile** sensor
  (reads the live schedule back from the device and tells you which profile is
  loaded — an exact match, not a guess).
- Service `aiprime_ble.deploy_profile` for automations.
- **Upload `.aip` files from within HA** (Settings → AI Prime → Configure):
  HA has no native file-upload card, so the integration provides a file picker
  that validates the profile and saves it to `/config/aiprime/profiles/`.

### Status & diagnostics
- Device metadata (manufacturer / model / serial / hardware + firmware +
  software revision), live **BLE RSSI**, **fan speed**, device-reported
  **channel discovery**, and a 30-second per-channel state poll.

### Optional Lovelace control subpage
- A ready-made glass-style control subpage (master toggle + RSSI chip, channel
  sliders, schedule select/load/upload, diagnostics) is available as splice
  blocks for the companion dashboard repo.

---

## Why this is hard (and what was built)

AI Prime HD lights speak a closed binary protocol over BLE. There is no vendor
API and no Home Assistant support. Everything here was reverse-engineered:

- **FSCI codec** (Framing Serial Connectivity Interface, an NXP convention) —
  a clean-room port from the pump-side project, confirmed **bit-identical**
  against the AI Prime's Qualcomm QCA4020 via a live round-trip test.
- **Live control path decoded** from an iOS PacketLogger capture of the myAI
  app: control is a single FSCI **SET attribute 407** carrying all channels at
  once, scale 0–1000, with channel `0x01` acting as a **master-enable** flag.
  (Earlier per-channel writes to attrs 1504/1513 silently no-op'd — those are
  read-only live-state views.)
- **Native schedule decoded** from a real myAI *deploy* capture: a 3-frame
  sequence (**SET attr 500** schedule + a 511 group, then two **attr 510**
  commits). Each schedule point is a 24-byte record
  `[time][flag][7×(channel, intensity)]`, intensity **clamped per channel**.
- **Schedule read-back** (GET attr 500) is decoded and matched deterministically
  back to a known `.aip` by regenerating its frames and comparing.
- **Channel → color map decoded** by slider testing + the deploy capture
  (the old Mobius slider-order heuristic was wrong).

### Verified, not vibes
The codec is covered by **offline byte-compare gates** that reconstruct real
captured app traffic and assert equality **including CRC** — so a regression
can't silently ship a malformed frame:

- `tests/test_schedule_deploy.py` — generated deploy frames vs the captured
  myAI deploy (msgid 8/9/10), byte-for-byte.
- `tests/test_schedule_readback.py` — schedule read decode + profile match,
  and the deploy framing builders vs the capture.
- `tests/test_profile_import.py` — `.aip` validate/save (incl. path-traversal
  guard).

Run them with no Home Assistant install: `python3 tests/test_schedule_deploy.py`.

---

## Hardware support

| Device | Status | Notes |
|---|---|---|
| AI Prime 16 Freshwater | 🟢 full control | Live control + native schedule deploy + upload, verified against MOBIUS at `1C:BC:EC:0A:E2:D0`. |
| AI Prime 16HD (saltwater) | 🟡 likely works | Channel set is discovered from the device, so a different color layout needs no code changes. Untested end-to-end. |
| AI Hydra HD | ❓ unknown | Different chip class — needs verification. |
| AI Nero pumps | ❌ out of scope | Use [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32) instead. |

---

## Architecture

```
AI Prime HD ◄──BLE/FSCI──► ESPHome Bluetooth Proxy ◄──WiFi──► Home Assistant
                            (ESP32 near tank)                   └─ this integration
```

The integration runs **inside Home Assistant** and uses HA's native `bluetooth`
integration to reach the light. If your HA host's onboard Bluetooth doesn't
reach the tank reliably (RSSI worse than ~-75 dBm), deploy an ESP32 running
[ESPHome Bluetooth Proxy](https://esphome.io/projects/bluetooth-proxy/) near the
tank — HA auto-discovers the proxy and routes BLE through it transparently. A
reference 3D-printed enclosure for the proxy lives in [`hardware/`](hardware/).

> **Proxy note:** the AI Prime's Qualcomm radio fragments BLE ACL payloads in a
> way that the ESP32 Bluedroid stack mishandles, dropping some FSCI
> notifications. The integration wraps each request in a retry layer
> (3 attempts) so reads/writes succeed reliably despite the drops; an ESP32-C6
> proxy is the most reliable host tested.

---

## Channels & color map

The Prime 16 Freshwater has **6 LED channels + 1 cooling fan / master line**,
identified by device-reported channel IDs. This map is **decoded** (slider
tests + deploy capture) — it supersedes the earlier Mobius slider-order guess:

| ID (hex) | Color / role | Exposed as |
|---|---|---|
| `0x11` | Blue | number (0–100%) |
| `0x13` | Green | number (0–100%) |
| `0x19` | Deep Red | number (0–100%) |
| `0x16` | Warm White | number (0–100%) |
| `0x10` | Cool White | number (0–100%) |
| `0x1E` | Moonlight | number (0–100%) |
| `0x01` | Fan / master-enable | sensor (fan speed) |

Channel discovery is dynamic: if the device reports an ID without a default
label, the integration auto-generates `Channel 0xNN` rather than dropping it —
so other Prime models with different layouts still work for reads.

> **Value scales:** live control writes use 0–1000 per-mille (attr 407); the
> device read scale is 0–20000 (attr 1504); `.aip` profiles use 0–2000. The
> integration converts internally and exposes 0–100% to you. Schedule
> intensities are clamped per channel (some HD channels allow higher drive than
> others) — decoded from the capture.

---

## Requirements

- Home Assistant `2025.1.0` or newer.
- HACS installed.
- The light's BLE MAC visible in HA's Bluetooth integration (Settings → Devices
  & Services → Bluetooth → look for `MOBIUS`).
- **Recommended:** an ESP32 BT proxy near the tank if your HA host's RSSI to the
  light is worse than ~-75 dBm.

## Installation (via HACS)

1. HACS → ⋮ menu → **Custom repositories**.
2. URL: `https://github.com/JLay2026/ha-aiprime-ble`. Category: **Integration**.
3. Install → restart Home Assistant.
4. The light auto-discovers via Bluetooth (the `local_name: MOBIUS` declaration
   triggers a discovery flow). Accept it. (Manual fallback: Add Integration →
   **AI Prime BLE**.)

## Using schedules (.aip)

1. In the myAI app, author your daily ramp and **export the `.aip`** profile.
2. Upload it: Settings → Devices & Services → **AI Prime → Configure →
   Import .aip profile** (or the **Upload new .aip profile** button on the
   control subpage). It's validated and saved to `/config/aiprime/profiles/`.
   You can also drop files there directly (Samba / File editor).
3. Pick it in the **Schedule profile** selector and press **Deploy / Load** —
   the light writes it to its on-device scheduler and runs it autonomously.
4. The **Active schedule** sensor confirms which profile is loaded.

---

## Roadmap

- 🔜 **Timed manual override** — set channels for a chosen duration then
  auto-revert to the schedule (needs the device override-timeout decode).
- 🔜 **Per-channel cap verification** — confirm the intensity clamp ceilings for
  blue / warm-white / moonlight with a capture that drives them past 1000.
- 🔮 Broader hardware coverage (16HD saltwater end-to-end, Hydra HD).

## Acknowledgments

- [mpshevlotsky/ai-pump-feed-esp32](https://github.com/mpshevlotsky/ai-pump-feed-esp32)
  — reverse-engineered AI's FSCI protocol on the pump side. The lighting product
  uses bit-identical framing / GATT UUIDs / CRC / message-ID semantics, so the
  transport codec was a near-direct lift; the lighting-specific control and
  schedule attributes were decoded separately here.
- The myAI app, whose BLE traffic (captured via iOS PacketLogger) made the
  control and schedule formats decodable.

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Aqua
Illumination** in any way. All product names and trademarks are the property of
their respective owners.

**USE AT YOUR OWN RISK.** No warranty. By using this integration you accept full
responsibility for any consequences to your livestock, equipment, or property.

## License

MIT
