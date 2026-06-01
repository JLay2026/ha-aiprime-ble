# Hardware changelog

All notable changes to the `hardware/` enclosure design.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [v0.6] — 2026-06-01

### Changed
- **Lid is now FLAT** (no drop-in lip). v0.5b lid lip didn't seat in physical print; user reverted to flat. Lid sits on top of bottom-shell outer edge; 4 counter-bores align with bottom posts.
- Top thickness unchanged (2 mm). Counter-bore depth 1 mm (M3 socket cap heads sit slightly proud).

## [v0.5b] — 2026-06-01

### Changed
- **USB-end screw posts reverted to v0.3 style** (in side-gaps near USB-end wall).
- **Antenna-end screw posts redesigned as PCB X-stop:** post body inner edge sits at PCB end (`x = pcb_x - post_outer_d/2`), so the PCB physically butts against them. Posts flank the SMA bulkhead laterally at Y = case_center ± (bulkhead_body_radius + post_outer_d/2 + 2 mm).
- Post positions restructured from Cartesian-product (`post_xs × post_ys`) to an explicit list of 4 (x, y) tuples.
- 18 mm antenna end-gap preserved — bulkhead has 10+ mm clear workspace between its inner end and PCB edge.

## [v0.5] — 2026-06-01 (internal)

### Changed
- **Antenna end-gap grown** from 8 → 18 mm (`pcb_end_gap_antenna`). Bulkhead now has substantial workspace.
- Antenna-end posts (briefly) tried near PCB edge — superseded by v0.5b layout per user diagram feedback.

## [v0.4] — 2026-06-01 (internal)

### Changed
- **Confirmed both USB-C ports top-mounted** (not stacked vertically as v0.3 had assumed). `below_pcb` halved 5.0 → 2.5 mm; case is ~2.5 mm shorter overall.
- USB-C cutout widened (`usbc_cutout_w` 19 → 23.4 mm) for both ports + cable-flange clearance simultaneously. Cutout Z realigned + Z-height shrunk (10 → 6 mm) since no bottom-hanging port to cover.
- End-gap split asymmetric: `pcb_end_gap_antenna = 8` / `pcb_end_gap_usbc = 0.5`. Antenna-end gap houses screw posts + bulkhead.
- Screw post topology revised: 2 posts moved from side-gaps to antenna end-gap (centered), 2 stayed in side-gaps at USB-end.

## [v0.3] — 2026-06-01 (internal)

### Changed
- Case shrunk to fit board snugly (`pcb_end_gap` 7 → 0.5 mm).
- USB-C cutout enlarged + repositioned (`usbc_cutout_h` 4.5 → 10 mm); aimed to cover assumed bottom-hanging port (later corrected in v0.4).
- Side passthrough then moved to short-end wall (router-style antenna mount).
- `below_pcb` raised 2.5 → 5.0 mm to clear assumed bottom port (later halved back in v0.4).

## [v0.2] — 2026-06-01 (internal)

### Changed
- Screw posts moved from end-gaps (v0.1) to side-gaps. M3 self-tap → M3 brass heat-set inserts (`post_pilot_d` 2.7 → 4.0 mm). Keyholes disabled by default.

## [v0.1.0] — 2026-06-01

Initial release. Shipped via [PR #1](https://github.com/JLay2026/ha-aiprime-ble/pull/1). See in-repo case.scad header and original README.

### Confirmed dimensions
- Board: 62.66 × 25.67 × 1.6 mm (Lonely Binary ESP32-S3 N16R8 Gold Edition IPEX, user-measured 2026-05-30).

## Final v0.6 geometry

| Param | Value |
|---|---|
| Outer dimensions | 87.16 × 41.67 × 18.6 mm assembled (16.6 mm bottom + 2 mm flat lid) |
| PCB position | x=21.00..83.66, y=8.00..33.67, z=4.50..6.10 |
| Antenna end-gap | 18 mm (PCB to inner antenna wall) |
| USB-C end-gap | 0.5 mm thermal slack only |
| SMA passthrough | 6.30 mm Ø on antenna-end short wall, Y=20.84 (centered), Z=9 (mid-cavity) |
| USB-C cutout | 23.4 × 6 mm on USB-end short wall |
| Antenna-end posts | (18.20, 11.54) and (18.20, 30.14) — PCB X-stop, flank SMA |
| USB-end posts | (75.66, 4.90) and (75.66, 36.77) — in side-gaps |
| Standoff height | 2.5 mm (both ports top-mounted, no bottom-port collision) |
| Hardware | M3 brass heat-set inserts (4.0 mm OD, 5 mm length); M3 × 8 mm socket-cap or button-head screws |
