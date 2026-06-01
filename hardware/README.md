# `hardware/` — printable enclosure for the BLE proxy

3D-printable case for the **Lonely Binary ESP32-S3 N16R8 Gold Edition (IPEX)** board running ESPHome Bluetooth Proxy firmware that bridges this integration to the AI Prime HD light over BLE.

## Files in this folder

| File | What it is |
|---|---|
| `case.scad` | Parametric OpenSCAD source. Open in [OpenSCAD](https://openscad.org) (free) to render, customize, or re-export STLs. |
| `aiprime-case-bottom.stl` | Bottom shell. PCB cradle, USB-C cutout, ventilation slots on the underside, wall-mount keyholes on the back, 4× corner screw posts. |
| `aiprime-case-lid.stl` | Default lid. Sharp 3 mm LED viewing hole, SMA antenna bulkhead hole on top, raised **"BLE PROXY"** label. |
| `aiprime-case-lid-diffuser.stl` | Alternate lid. Same as default, plus an 8 mm × 1.2 mm recessed well around the LED hole for a designed "downlight" look. |
| `README.md` | This file. |

Pick **one** lid — print the default unless you specifically want the recessed-LED look.

## Quick start (Bambu Studio + X1C)

1. Open Bambu Studio.
2. Import `aiprime-case-bottom.stl` and one of the two lid STLs.
3. Slice with the profile in the [Print profile](#print-profile-x1c-ams-aware) section below.
4. Print.
5. Assemble per [Assembly](#assembly).

Total print time: ~45 minutes per part on an X1C at 0.20 mm layer.

## Bill of materials

- **Lonely Binary ESP32-S3 N16R8 Gold Edition IPEX** ([Amazon B0FFLXM9KL](https://www.amazon.com/ESP32-S3-Development-16MB-IPEX-Antenna/dp/B0FFLXM9KL)) — the board this case is sized for
- **4 × M3 × 8 mm self-tapping screws** (the type used for plastic enclosures — flat or pan head). Bambu's hardware kit, McMaster 96485A220, or any "M3 thread-forming for plastic" works.
- **IPEX→SMA bulkhead pigtail + SMA-male duck antenna** — both included in the Lonely Binary kit
- **Optional:** 4 × M3 brass heat-set inserts (~3.5 mm OD, 4 mm length) — see [Heat-set insert upgrade](#heat-set-insert-upgrade) below
- **Optional:** 2 × #6 wood screws or wall anchors for the back keyhole mount

## Print profile (X1C, AMS-aware)

| Setting | Value | Why |
|---|---|---|
| Slicer | Bambu Studio | matches the X1C workflow used elsewhere in this household |
| Printer | Bambu Lab X1C | |
| Nozzle | 0.4 mm (standard) | |
| Layer height | **0.20 mm** | strength + surface balance — case isn't precision optical |
| Wall loops | **4** | strong enough for M3 self-tapping screws into PLA |
| Top/bottom layers | **5 / 5** | sealed enough that splashes don't ingress easily |
| Infill | **15% gyroid** | rigid; gyroid is isotropic and looks decent through the vents |
| Supports | Tree (auto, organic), only if Bambu Studio suggests | the 6.5 mm SMA hole on the lid is borderline self-supporting at 0.4 mm nozzle — accept supports if offered |
| Brim | Off (or 3 mm if first-layer is iffy) | small footprint may want a brim |
| Build plate | Smooth PEI or textured PEI | textured hides kitchen-grime scuffs |

### Recommended filament

| Tier | Filament | Why |
|---|---|---|
| **Default** | Bambu Lab or PolyLite PLA in matte black | Hides handling marks, neutral in kitchen, prints clean on X1C |
| **Bronze accent for the label** | Bambu PLA Metal bronze (or any metallic) | Bambu Studio's "Color Painting" can pick out just the raised "BLE PROXY" text in this color via AMS swap mid-print |
| **For a wetter install** (above tank, splash zone) | White or clear PETG | Better humidity tolerance than PLA; reprint if the install ends up directly in the spray zone |

**Print orientation:**
- **Bottom shell:** prints with its open top facing up (no rotation needed). Floor-down.
- **Lid:** the STL is exported lip-down. Bambu Studio's auto-orient should keep it that way — lip on the build plate, top face up.

## Assembly

1. **Verify the print fits the PCB** — drop the board into the bottom shell. Should sit on the four corner standoffs with USB-C ports aligned to the cutout and ~0.5 mm clearance around the perimeter. If it's too tight, increase `pcb_xy_clearance` in `case.scad` (0.6 → 0.8 mm) and reprint.
2. **Mount the SMA bulkhead** in the lid's 6.5 mm hole. The pigtail's SMA-female nut goes on the OUTSIDE; washer and lock-nut on the INSIDE. Snug — don't crank.
3. **Connect the IPEX pigtail end** to the board's uFL connector. These are fragile — push straight down with a fingernail until it clicks. Pull only with the cable, never the connector body.
4. **Route cables:** USB-C exits the short-edge cutout. Loop a small velcro tie around the interior strain-relief post and the cable to take tension off the connector.
5. **Drop the lid** onto the bottom shell. The lid lip slides into the bottom cavity with a snug-but-not-forced fit. If too tight, raise `lid_lip_clearance` in `case.scad` (0.4 → 0.6 mm).
6. **Drive four M3 × 8 mm self-tapping screws** through the lid's counter-bored holes into the bottom shell's corner posts. Hand-tight only — over-torquing strips the plastic threads.
7. **Screw on the antenna** to the outside of the SMA bulkhead.

## Wall mounting

Two keyholes on the back face. Drive two #6 wood screws (or wall anchors) into the wall **38 mm apart** (matches `keyhole_spacing` in `case.scad`). Leave heads ~5 mm proud. Slide the case down onto them — the shaft rides into the slot, the head pulls the case flat.

For shelf-sit installs the keyholes are harmless — they're hidden against the back.

## Heat-set insert upgrade

If you expect to open the case more than 2–3 times (reflashing, swapping boards), M3 self-tap will eventually strip. Migration:

1. In `case.scad`, change `post_pilot_d = 2.7;` → `post_pilot_d = 4.0;`. Re-render and reprint the bottom shell.
2. Buy M3 brass heat-set inserts, 4 mm OD × 4 mm length (McMaster 95001A203 or similar).
3. Heat each insert with a soldering iron at ~250 °C. Press straight down into the post until flush.
4. Switch to M3 × 8 mm machine screws (not self-tap) from then on.

After the upgrade the case handles 50+ open/close cycles.

## Regenerating the STLs from `case.scad`

If you change dimensions or want a custom variant, regenerate with OpenSCAD (CLI):

```bash
openscad -o aiprime-case-bottom.stl       -D 'render_part="bottom"'         case.scad
openscad -o aiprime-case-lid.stl          -D 'render_part="lid"'            case.scad
openscad -o aiprime-case-lid-diffuser.stl -D 'render_part="lid_diffuser"'   case.scad
```

GUI workflow: open `case.scad`, edit the `render_part` line near the bottom, F6 to render, File → Export → STL.

## Parameter cheatsheet (top of `case.scad`)

| Param | Default | Tweak if… |
|---|---|---|
| `pcb_length` / `pcb_width` | 62.66 / 25.67 | board dimensions (verified for current Lonely Binary revision) |
| `pcb_xy_clearance` | 0.6 | board too tight (raise to 0.8) or too loose (drop to 0.4) |
| `pcb_end_gap` | 7.0 | corner screw posts feel cramped (raise to 8) — also resizes the case |
| `usbc_cutout_w` | 19.0 | cable shells won't seat (raise to 21) |
| `sma_hole_d` | 6.5 | your SMA bulkhead nut needs a tighter or looser fit |
| `sma_hole_offset_x` | 18 | antenna hole doesn't land over the IPEX pad — measure your board |
| `led_window_offset_x` | 18 | LED hole misses the onboard LED — measure your board |
| `lid_lip_clearance` | 0.4 | lid too tight (raise to 0.6) or rattles (drop to 0.3) |
| `keyhole_spacing` | 38 | match to your wall-screw spacing if already drilled |
| `label_text` | `"BLE PROXY"` | customize the embossed label, or set `label_enabled = false` |
| `label_size` | 4.5 | bigger / smaller text |
| `diffuser_solid_floor` (lid_diffuser only) | false | true requires translucent filament (clear PETG ideal) |

## Outer dimensions (computed)

| Axis | mm |
|---|---|
| Outer length (antenna to USB-C) | **82.66** |
| Outer width | **31.67** |
| Bottom shell height | ~14.6 |
| Lid height | ~5.0 |
| **Assembled height** | **~17** |

Trivially fits the X1C build plate (256 × 256 mm).

## Known limitations / TODOs

- [ ] **No IP rating.** Splash-resistant from above (top is solid, vents face down), but not waterproof. Don't install where condensation can pool.
- [ ] **Antenna position is fixed on the TOP face.** To exit a side instead, edit `sma_hole_offset_x` and reposition the cylinder inside `case_lid()`.
- [ ] **LED viewing is a sharp hole** (or shallow well in the diffuser variant). For real diffusion, print the diffuser lid in clear/translucent PETG and set `diffuser_solid_floor = true`.
- [ ] **Untested print as of v0.1** — first physical fitting may surface dimension tweaks. Update `case.scad` parameters and reprint as needed.

## Acknowledgments

Design influenced by enclosure conventions from the wider HA / ESPHome community (Olimex ESP32-PoE enclosures, the chihiros-led-control project's hardware notes). MIT licensed.
