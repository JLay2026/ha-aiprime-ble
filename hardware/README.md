# `hardware/` — printable enclosure for the BLE proxy

3D-printable case for the **Lonely Binary ESP32-S3 N16R8 Gold Edition (IPEX)** board running ESPHome Bluetooth Proxy firmware that bridges this integration to the AI Prime HD light over BLE.

**Current version: v0.6** (2026-06-01). See `CHANGELOG.md` for iteration history.

## Files in this folder

| File | What it is |
|---|---|
| `case.scad` | Parametric OpenSCAD source. Compile to STL via CLI or OpenSCAD GUI. |
| `aiprime-case-bottom-v0.6.stl` | Bottom shell. Print first. |
| `aiprime-case-lid-v0.6.stl` | Flat lid. Sits on top of bottom shell. |
| `CHANGELOG.md` | Version-by-version evolution. |
| `README.md` | This file. |

Legacy v0.1 STLs in `stl/` and `stl-ascii/` are superseded by the v0.6 STLs at the folder root.

## Quick start (Bambu Studio + X1C)

1. Import both v0.6 STLs into Bambu Studio.
2. Slice with the profile below.
3. Print.
4. Press M3 brass heat-set inserts into the 4 bottom-shell posts (soldering iron at ~250 °C).
5. Drop the PCB in.
6. Mount the SMA bulkhead through the antenna-end short wall.
7. Drop the flat lid on, drive 4 M3 × 8 mm screws.

## Bill of materials

- **Lonely Binary ESP32-S3 N16R8 Gold Edition IPEX** ([Amazon B0FFLXM9KL](https://www.amazon.com/ESP32-S3-Development-16MB-IPEX-Antenna/dp/B0FFLXM9KL))
- **4 × M3 brass heat-set inserts**, 4.0 mm OD, 5 mm length ([Ruthex RX-M3 × 5](https://www.amazon.com/dp/B086Z5R7XF) or McMaster `93365A140`)
- **4 × M3 × 8 mm socket-cap screws** (or button-head for fully-flush look)
- **SMA bulkhead + IPEX-to-SMA pigtail + 2.4 GHz duck antenna** (included in the Lonely Binary kit)
- **Soldering iron** for installing the brass inserts (any iron + generic tip works; Hakko T18-S6 cleaner)

## Print profile (X1C, AMS-aware)

| Setting | Value |
|---|---|
| Slicer | Bambu Studio |
| Printer | Bambu Lab X1C |
| Nozzle | 0.4 mm |
| Layer height | 0.20 mm |
| Wall loops | 4 |
| Top/bottom layers | 5 / 5 |
| Infill | 15% gyroid |
| Supports | Tree (auto) if Bambu Studio suggests; usually self-supporting |
| Brim | Off (or 3 mm if first-layer adhesion is iffy) |

### Filament

- **Default:** PolyLite PLA matte black (AMS 3 tray 4). Both bottom and lid.
- **Optional accent:** Bronze metallic (AMS 3 tray 3) painted just on the raised "BLE PROXY" lid label via Bambu Studio's Color Painting tool (~30 sec setup, +10–15 min print time).
- **For wetter installs:** White or clear PETG — better humidity tolerance, requires PETG profile (bed 80 °C, nozzle 250 °C).

## Final geometry (v0.6)

```
             ANTENNA END (SMA passthrough on short wall)
             │
             ▼  18 mm clearance to PCB
   ┌──────────────────────────────────────────────────┐
   │  ○ SMA ○              ┌─────────────────────────┐   ○         │
   │         ○              │                       │             │
   │                         │         PCB           │             │
   │                         │                       │             │
   │         ○              │                       │             │
   │                         └─────────────────────────┘   ○         │
   └───────────────────────────────────────────────────┘
                                       ▲ USB-C end (cutout in short wall)
```

- `○` antenna-end posts — PCB butts up against them as X-stop, flank SMA bulkhead
- Two more posts at USB-end (in side-gaps, near USB short wall)

## Outer dimensions

| Axis | mm |
|---|---|
| Length (antenna to USB-C) | **87.16** |
| Width | **41.67** |
| Height (bottom shell) | 16.6 |
| Height (lid) | 2.0 |
| **Assembled height** | **~18.6** |

## Generating STLs from `case.scad`

```bash
openscad -o aiprime-case-bottom-v0.6.stl -D 'render_part="bottom"' case.scad
openscad -o aiprime-case-lid-v0.6.stl    -D 'render_part="lid"'    case.scad
```

Default `render_part="preview"` shows both side-by-side in the OpenSCAD GUI (F6).

## Tunable parameters (top of `case.scad`)

| Param | Default | Tweak if… |
|---|---|---|
| `pcb_length` / `pcb_width` | 62.66 / 25.67 | board dimensions — verify with calipers |
| `pcb_xy_clearance` | 0.6 | PCB too tight (raise to 0.8) or too loose (drop to 0.4) |
| `pcb_end_gap_antenna` | 18 | bulkhead crowds PCB (raise) or wasted space (lower carefully) |
| `pcb_end_gap_usbc` | 0.5 | USB cables don't seat (raise to 1) |
| `pcb_side_gap` | 5 | PCB binds against USB-end posts (raise to 5.5) |
| `usbc_cutout_w` / `_h` | 23.4 / 6 | cables don't fit (widen w; raise h if needed) |
| `sma_side_d` | 6.30 | bulkhead binds (raise to 6.40) or sloppy (drop to 6.20) |
| `sma_side_z` | 9.0 | bulkhead vertical position wrong |
| `below_pcb` | 2.5 | PCB sits too high/low; raise if you have bottom-mounted ports |
| `post_pilot_d` | 4.0 | swap to 2.7 for M3 self-tap (no inserts) |
| `antenna_post_nut_clearance` | 2.0 | hex-nut wrench doesn't fit (raise to 3) |
| `label_text` | `"BLE PROXY"` | rename or disable (`label_enabled = false`) |

## Known limitations / TODOs

- [ ] No IP rating. Splash-resistant from above; don't install where condensation can pool.
- [ ] Flat lid has no seal at the seam. Add a thin foam gasket if needed.
- [ ] Lid label rendering depends on `Liberation Sans:style=Bold` being installed locally for OpenSCAD. Substitute via `label_font` if missing.
- [ ] Diffuser-well lid variant (`case_lid_with_diffuser_well`) is currently a stub — falls back to the standard flat lid. To be revisited when there's demand.

## Acknowledgments

Design influenced by enclosure conventions from the wider HA / ESPHome community. MIT licensed.
