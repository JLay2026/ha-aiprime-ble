# Hardware changelog

All notable changes to the `hardware/` enclosure design will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) / parallel to the integration's repo-root `CHANGELOG.md`.

## [Unreleased]

Captures test-print feedback against v0.1.0. Update each line as it's resolved:

- [ ] **First-fit dimensions** — PCB drop-in fit (clearance perimeter), USB-C port alignment, screw-pilot bite. Adjust `pcb_xy_clearance`, USB cutout z-offset, or `post_pilot_d` as needed.
- [ ] **Wall-mount keyhole drop direction** — verify slot drops in the right direction relative to your intended install orientation.
- [ ] **Diffuser-well lid** — first impression of the recessed-LED look once one is printed.

## [v0.1.0] — 2026-06-01

Initial release. Shipped via [PR #1](https://github.com/JLay2026/ha-aiprime-ble/pull/1).

### Added

- `case.scad` — parametric OpenSCAD source, three printable parts selectable via `render_part` CLI parameter (`bottom`, `lid`, `lid_diffuser`).
- `aiprime-case-bottom.stl` — bottom shell. PCB cradle with 4 corner standoffs + 4 corner screw posts (M3 self-tap, 2.7 mm pilot), USB-C cutout on one short edge, 5 ventilation slots on the underside, 2 wall-mount keyholes on the back long-edge wall, interior strain-relief post.
- `aiprime-case-lid.stl` — default lid. SMA antenna bulkhead hole (6.5 mm) on the top face, 3 mm LED viewing hole, 4 counter-bored M3 through-holes, lip drops into bottom shell with 0.4 mm clearance, raised "BLE PROXY" label (4.5 mm Liberation Sans Bold, 1 mm relief).
- `aiprime-case-lid-diffuser.stl` — alternate lid. Same as default plus an 8 mm × 1.2 mm recessed well around the LED hole. Default mode is through-hole ("designed downlight" look in any color); `diffuser_solid_floor = true` mode requires translucent filament for a soft-glow look.
- `README.md` — print profile tuned for Bambu Lab X1C (0.20 mm layer, 4 walls, 15% gyroid), AMS-aware filament recommendation (PolyLite PLA matte black default, bronze metallic accent option for the label), assembly steps, parameter cheatsheet, heat-set insert upgrade path.

### Confirmed dimensions

- Board: **62.66 × 25.67 × 1.6 mm** (Lonely Binary ESP32-S3 N16R8 Gold Edition IPEX, user-measured 2026-05-30).
- Case outer: **82.66 × 31.67 × ~17 mm** assembled.
- Build-plate fit: trivially fits the X1C's 256 × 256 mm bed.

### Design decisions

- **2-piece, 4× M3 self-tap** assembly chosen over snap-fit. Self-tap holds 2–3 disassembly cycles cleanly; heat-set insert upgrade path documented for higher cycle counts.
- **End-gap geometry** (`pcb_end_gap = 7.0 mm` at each short axis end) so the 4 corner screw posts sit BESIDE the PCB rather than overlapping its footprint. Earlier internal v0.0.x drafts assumed shorter PCB length and had posts colliding with the PCB area — caught during sanity-checking after user-confirmed dimensions came in.
- **Antenna on TOP face**, not a side. Cleanest install — antenna sticks up like a router.
- **Vents face DOWN.** Splash from above doesn't enter the case (kitchen install consideration).
- **ASCII STL format** rather than binary. Larger files, but text-diffable and reviewable.

## [v0.0.x] — internal, pre-release

Not committed to the repo. Captured here for design lineage:

- **Estimated dimensions** (55 × 25.4 mm) from product photos before user-measurement. Resulted in screw posts overlapping the PCB area — caught + corrected in v0.1.0 by adding `pcb_end_gap`.
- **OpenSCAD vs CadQuery decision:** OpenSCAD won for text-diffability and lower setup friction. CadQuery briefly installed in the sandbox as a fallback before the OpenSCAD AppImage approach worked.
- **AppImage installation approach** for sandboxed compilation: `wget` the AppImage from files.openscad.org, extract with `--appimage-extract`, run `./squashfs-root/AppRun`. Sandboxed environments without sudo can't `apt install` openscad, but the AppImage bundles all deps.
