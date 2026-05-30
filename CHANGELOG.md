# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Initial scaffold mirroring the `JLay2026/ha-hydros` repo layout.
- HACS-installable shell that loads cleanly but exposes no entities yet.
- Bluetooth discovery in `manifest.json` matching `local_name: "MOBIUS"`.
- Constants for known channel IDs and FSCI attribute IDs derived from the Mobius app's Settings Dump.

### Pending (Days 3–5 of the project plan)
- Wire-format validation test (FSCI framing against the AI Prime).
- `aiprime_hub.py` BLE connection lifecycle implementation.
- `light.py` aggregate brightness entity.
- `number.py` per-channel sliders (6 LED channels).
- `sensor.py` RSSI, firmware, fan state.
- Empirical channel-to-color discovery flow.

## [0.0.1] — 2026-05-29

- Repository scaffolded.
