// =============================================================================
// ESP32-S3 BT Proxy enclosure for the Lonely Binary N16R8 Gold Edition (IPEX)
//
// Two-piece M3-screwed case sized for an under-cabinet / wall-shelf install
// next to the Waterbox25 fishtank. Designed to print on a Bambu Lab X1C.
//
// Open in OpenSCAD (free, openscad.org), render with F6, export each part
// separately by commenting out the other in the RENDER section at the bottom.
// All dimensions are millimeters.
//
// Source-of-truth for the BLE proxy this case houses:
//   - JLay2026/ha-aiprime-ble  (HACS integration)
//   - github.com/esphome/bluetooth-proxies  (firmware)
//
// PARAMETERS — measure your board before printing and adjust if these don't
// match yours. Tolerances are designed loose (±0.5 mm) to forgive small drift.
// =============================================================================


// ---- PCB ---------------------------------------------------------------------
// Lonely Binary ESP32-S3 N16R8 Gold Edition IPEX — measured 2026-05-30.
pcb_length          = 62.66;  // long edge (user-measured)
pcb_width           = 25.67;  // short edge (user-measured)
pcb_thickness       = 1.6;    // PCB substrate
pcb_xy_clearance    = 0.6;    // air gap around PCB perimeter (each side)

// Extra interior length at each short edge — accommodates corner screw posts
// so they sit BESIDE the PCB rather than overlapping its footprint. Set to at
// least (post_outer_d/2 + 1) to give the post body clearance from the PCB.
pcb_end_gap         = 7.0;    // mm of slack on each short axis end (USB-C end + antenna end)


// ---- USB-C cutout ------------------------------------------------------------
// Both Type-C ports sit side-by-side on ONE short edge of the PCB.
// Single rectangular hole covers both; deep enough to allow plugged-in cables.
usbc_cutout_w       = 19.0;   // along PCB short axis (covers both ports + slack)
usbc_cutout_h       = 4.5;    // tall enough for a Type-C cable shell
usbc_cutout_y_nudge = 0;      // shift toward one side if your ports aren't centered


// ---- SMA antenna bulkhead ----------------------------------------------------
// The kit's IPEX→SMA pigtail mounts through the case wall; antenna screws on
// from outside. Default placement: on the TOP face, near the WROOM end of PCB.
// 6.35 mm = standard SMA-female bulkhead thread (1/4-36 UNS-2A). Add 0.15 mm
// slop for an M0.5 nut grip → 6.5 mm hole.
// Offset is measured from the antenna-end case wall (long axis), so it includes
// the wall + end-gap + a little inset to land above the WROOM module's IPEX pad.
sma_hole_d          = 6.5;
sma_hole_offset_x   = 18;     // distance from antenna-end case wall (= wall+end_gap+~9 inset onto PCB)


// ---- Status LED window -------------------------------------------------------
// Small viewing hole over the onboard RGB LED so blink patterns are visible.
// If you'd rather diffuse it, leave the hole and glue a 3 mm clear PETG plug.
// Offset is measured from the USB-C-end case wall (= wall+end_gap+~9 inset onto PCB).
led_window_d        = 3.2;
led_window_offset_x = 18;     // distance from USB-C-end case wall


// ---- Walls and corners -------------------------------------------------------
wall                = 2.4;    // 6 perimeters at 0.4 nozzle / 0.4 line width
top_thickness       = 2.0;
bottom_thickness    = 2.0;
corner_r            = 2.5;    // outer radius — print-friendly chamfer


// ---- Internal vertical clearances --------------------------------------------
above_pcb           = 7.5;    // for IPEX pigtail routing + USB-C cables
below_pcb           = 2.5;    // airflow under PCB + raised by standoff posts


// ---- Lid fit -----------------------------------------------------------------
lid_lip_h           = 3.0;    // how much the lid drops into the bottom shell
lid_lip_clearance   = 0.4;    // total play between lip and inner wall (XY)


// ---- Screw posts (M3 self-tapping) -------------------------------------------
// 4× M3 × 8 mm self-tapping screws. PLA holds 2–3 disassembly cycles cleanly.
// For more cycles: drill out to 4.0 mm and press in M3 brass heat-set inserts;
// see post_pilot_d alternative below.
post_outer_d        = 5.6;
post_pilot_d        = 2.7;    // M3 self-tap pilot. Use 4.0 for heat-set inserts.
post_inset          = 3.5;    // from inner wall to post center


// ---- Wall-mount keyholes (BACK long-edge wall) -------------------------------
// Two slotted keyhole openings so the case hangs on two screw heads pre-driven
// into the wall. Drop a #6 wall screw to about 5 mm proud, slide the case down.
keyhole_enabled     = true;
keyhole_spacing     = 38;     // center-to-center between the two holes
keyhole_head_d      = 7.5;    // upper round hole (screw head clears)
keyhole_shaft_d     = 3.5;    // lower slot (screw shank rides in)
keyhole_drop        = 6;      // distance from round center down to slot bottom


// ---- Ventilation slots (UNDERSIDE) -------------------------------------------
// On the bottom face only — kitchen splashes drip past, not in.
vent_count          = 5;
vent_slot_length    = 22;
vent_slot_width     = 1.6;
vent_slot_spacing   = 3.6;


// ---- Strain-relief post ------------------------------------------------------
// Small interior post next to the USB cutout; loop a velcro tie around it to
// take cable tension off the USB-C connectors.
strain_relief_enabled = true;


// ---- Top-face label (raised relief) ------------------------------------------
// "BLE PROXY" extruded above the lid's top face. Sits in the open zone between
// the SMA bulkhead hole and the LED window. In Bambu Studio, use the Color
// Painting tool to paint just this text in a contrasting AMS color (e.g.
// bronze metallic from AMS3 tray 3) for a clean two-tone print.
label_enabled = true;
label_text    = "BLE PROXY";
label_size    = 4.5;          // mm font height
label_relief  = 1.0;          // mm extruded above top face
label_font    = "Liberation Sans:style=Bold";   // any installed font works


// =============================================================================
// DERIVED — don't edit below unless you know what you're doing
// =============================================================================

// Outer case dimensions
// pcb_end_gap on each short-axis end gives screw posts room to live BESIDE
// the PCB without intruding into its footprint. Long-axis sides stay tight.
inner_l = pcb_length + 2 * pcb_xy_clearance + 2 * pcb_end_gap;
inner_w = pcb_width  + 2 * pcb_xy_clearance;
case_l  = inner_l + 2 * wall;
case_w  = inner_w + 2 * wall;

// Bottom shell height (inner cavity + bottom thickness)
inner_h_bottom = below_pcb + pcb_thickness + above_pcb;
bottom_h       = inner_h_bottom + bottom_thickness;

// Lid height (top thickness + lip drop)
lid_h          = top_thickness + lid_lip_h;

// PCB position inside the cavity — centered, offset by wall + end_gap from
// the long-axis short edges.
pcb_x = wall + pcb_end_gap + pcb_xy_clearance;
pcb_y = wall + pcb_xy_clearance;
pcb_z = bottom_thickness + below_pcb;

// Screw post centers (4 corners — but living in the end-gap zones, NOT in the
// PCB footprint). Center each post in the end-gap on the x-axis, and tuck up
// against the long-edge walls on the y-axis with a small inset.
post_xs = [wall + pcb_end_gap / 2,
           case_l - wall - pcb_end_gap / 2];
post_ys = [wall + post_outer_d / 2 + 0.5,
           case_w - wall - post_outer_d / 2 - 0.5];


// =============================================================================
// PRIMITIVES
// =============================================================================

module rounded_box(L, W, H, r) {
    hull() {
        for (x = [r, L - r], y = [r, W - r])
            translate([x, y, 0])
                cylinder(r = r, h = H, $fn = 64);
    }
}

module rounded_pocket(L, W, H, r) {
    // Same as rounded_box but ensures r doesn't go non-positive on small pockets
    rr = max(0.5, r);
    hull() {
        for (x = [rr, L - rr], y = [rr, W - rr])
            translate([x, y, 0])
                cylinder(r = rr, h = H, $fn = 48);
    }
}


// =============================================================================
// BOTTOM SHELL
// =============================================================================

module case_bottom() {
    difference() {
        // outer shell
        rounded_box(case_l, case_w, bottom_h, corner_r);

        // interior cavity (open top — lid closes it)
        translate([wall, wall, bottom_thickness])
            rounded_pocket(inner_l, inner_w, inner_h_bottom + 1, corner_r - wall);

        // USB-C cutout on +X short edge (antenna end is -X)
        translate([
            case_l - wall - 0.1,
            (case_w - usbc_cutout_w) / 2 + usbc_cutout_y_nudge,
            pcb_z + pcb_thickness - 1.5   // align cable centerline with PCB top
        ])
            cube([wall + 0.2, usbc_cutout_w, usbc_cutout_h]);

        // ventilation slots on underside
        total_vent_w = vent_count * vent_slot_width + (vent_count - 1) * vent_slot_spacing;
        vent_y0 = (case_w - total_vent_w) / 2;
        for (i = [0 : vent_count - 1]) {
            translate([
                (case_l - vent_slot_length) / 2,
                vent_y0 + i * (vent_slot_width + vent_slot_spacing),
                -0.1
            ])
                cube([vent_slot_length, vent_slot_width, bottom_thickness + 0.2]);
        }

        // wall-mount keyholes on the BACK long-edge wall (+Y wall)
        if (keyhole_enabled) {
            kh_z = bottom_h * 0.55;
            for (dx = [-keyhole_spacing / 2, keyhole_spacing / 2]) {
                translate([case_l / 2 + dx, case_w - wall - 0.05, kh_z]) {
                    rotate([-90, 0, 0]) {
                        // round head opening
                        cylinder(r = keyhole_head_d / 2, h = wall + 0.2, $fn = 48);
                        // shaft slot dropping down (in print orientation, slot
                        // is BELOW the round hole when case hangs from screws)
                        translate([-keyhole_shaft_d / 2, -keyhole_drop, 0])
                            cube([keyhole_shaft_d, keyhole_drop, wall + 0.2]);
                    }
                }
            }
        }
    }

    // 4× PCB-rest standoffs (1.2 mm tall above cavity floor)
    standoff_inset = 4;
    for (sx = [pcb_x + standoff_inset, pcb_x + pcb_length - standoff_inset],
         sy = [pcb_y + standoff_inset, pcb_y + pcb_width  - standoff_inset])
    {
        translate([sx, sy, bottom_thickness])
            cylinder(r = 1.5, h = below_pcb, $fn = 24);
    }

    // 4× screw posts at corners (hollow cylinders with M3 self-tap pilot)
    for (px = post_xs, py = post_ys) {
        translate([px, py, bottom_thickness])
            difference() {
                cylinder(r = post_outer_d / 2, h = inner_h_bottom, $fn = 32);
                translate([0, 0, -0.1])
                    cylinder(r = post_pilot_d / 2, h = inner_h_bottom + 0.2, $fn = 24);
            }
    }

    // Strain-relief post — small interior column near the USB cutout
    if (strain_relief_enabled) {
        sr_x = case_l - wall - 6;
        sr_y = wall + 4;
        translate([sr_x, sr_y, bottom_thickness])
            cylinder(r = 1.8, h = inner_h_bottom * 0.45, $fn = 24);
    }
}


// =============================================================================
// LID
// =============================================================================

module case_lid() {
    union() {
        difference() {
            union() {
                // Top plate
                rounded_box(case_l, case_w, top_thickness, corner_r);

                // Lid lip (drops into the bottom shell's cavity)
                translate([wall + lid_lip_clearance / 2,
                           wall + lid_lip_clearance / 2,
                           -lid_lip_h])
                    rounded_pocket(
                        inner_l - lid_lip_clearance,
                        inner_w - lid_lip_clearance,
                        lid_lip_h,
                        corner_r - wall - lid_lip_clearance / 2
                    );
            }

            // SMA antenna bulkhead hole (top face, near the antenna end)
            translate([sma_hole_offset_x, case_w / 2, -lid_lip_h - 0.5])
                cylinder(r = sma_hole_d / 2, h = top_thickness + lid_lip_h + 1, $fn = 48);

            // Status LED window (top face, near the USB-C end)
            translate([case_l - led_window_offset_x, case_w / 2, -lid_lip_h - 0.5])
                cylinder(r = led_window_d / 2, h = top_thickness + lid_lip_h + 1, $fn = 32);

            // 4× M3 shaft clearance through lid + counter-bore for screw head
            for (px = post_xs, py = post_ys) {
                // through-hole
                translate([px, py, -lid_lip_h - 0.5])
                    cylinder(r = 1.7, h = top_thickness + lid_lip_h + 1, $fn = 24);
                // counter-bore for an M3 socket-cap head (5.5 mm head, 3 mm tall)
                translate([px, py, top_thickness - 2.0])
                    cylinder(r = 3.0, h = 2.5, $fn = 32);
            }
        }

        // Raised "BLE PROXY" label on top face, centered between SMA + LED
        if (label_enabled) {
            translate([case_l / 2, case_w / 2, top_thickness])
                linear_extrude(height = label_relief)
                    text(label_text,
                         size    = label_size,
                         font    = label_font,
                         halign  = "center",
                         valign  = "center");
        }
    }
}


// =============================================================================
// ALTERNATE LID — recessed well around the status LED
// =============================================================================
// Identical to case_lid() except the LED window is recessed into a wider, shallower
// well on the top face. Two use modes:
//
//   1. Print in OPAQUE filament (your default matte black, etc.):
//      The 3 mm through-hole still passes the LED light directly; the well just
//      gives the LED a designed "recessed downlight" look instead of a raw hole
//      flush with the surface. Optional: fill the well with epoxy or AMS-painted
//      bronze for a clean accent ring.
//
//   2. Print in TRANSLUCENT/CLEAR filament (white PETG works passably; buy a
//      clear PETG roll for the best result), and leave the diffuser_solid_floor
//      flag true. The well's floor stays solid (no through-hole), and the LED
//      shines through ~0.6 mm of clear plastic for a soft glow.
//
// Pick which mode you want by setting `diffuser_solid_floor` below.

diffuser_well_d            = 8.0;    // outer diameter of the recess (visible ring)
diffuser_well_depth        = 1.2;    // how deep the recess goes
diffuser_solid_floor       = false;  // false = keep the 3mm LED through-hole; true = solid floor (needs translucent filament)
diffuser_floor_thickness   = 0.6;    // only used when diffuser_solid_floor = true

module case_lid_with_diffuser_well() {
    led_x = case_l - led_window_offset_x;
    led_y = case_w / 2;
    union() {
        difference() {
            union() {
                rounded_box(case_l, case_w, top_thickness, corner_r);
                translate([wall + lid_lip_clearance / 2,
                           wall + lid_lip_clearance / 2,
                           -lid_lip_h])
                    rounded_pocket(
                        inner_l - lid_lip_clearance,
                        inner_w - lid_lip_clearance,
                        lid_lip_h,
                        corner_r - wall - lid_lip_clearance / 2
                    );
            }

            // SMA hole (unchanged)
            translate([sma_hole_offset_x, case_w / 2, -lid_lip_h - 0.5])
                cylinder(r = sma_hole_d / 2, h = top_thickness + lid_lip_h + 1, $fn = 48);

            // LED through-hole (only when NOT using a solid diffuser floor)
            if (!diffuser_solid_floor) {
                translate([led_x, led_y, -lid_lip_h - 0.5])
                    cylinder(r = led_window_d / 2,
                             h = top_thickness + lid_lip_h + 1,
                             $fn = 32);
            }

            // Recessed well on the top face around the LED
            translate([led_x, led_y, top_thickness - diffuser_well_depth])
                cylinder(r = diffuser_well_d / 2,
                         h = diffuser_well_depth + 0.01,
                         $fn = 48);

            // If solid-floor mode, partially excavate from BELOW the well to
            // leave only `diffuser_floor_thickness` of plastic to glow through.
            if (diffuser_solid_floor) {
                excavate_h = top_thickness - diffuser_well_depth - diffuser_floor_thickness;
                if (excavate_h > 0.1) {
                    translate([led_x, led_y, -lid_lip_h - 0.5])
                        cylinder(r = (diffuser_well_d / 2) - 0.5,   // slightly smaller so the floor has structure at its edge
                                 h = lid_lip_h + 0.5 + excavate_h,
                                 $fn = 48);
                }
            }

            // 4× M3 shaft clearance + counter-bores (unchanged from case_lid)
            for (px = post_xs, py = post_ys) {
                translate([px, py, -lid_lip_h - 0.5])
                    cylinder(r = 1.7, h = top_thickness + lid_lip_h + 1, $fn = 24);
                translate([px, py, top_thickness - 2.0])
                    cylinder(r = 3.0, h = 2.5, $fn = 32);
            }
        }

        // Same raised label as case_lid()
        if (label_enabled) {
            translate([case_l / 2, case_w / 2, top_thickness])
                linear_extrude(height = label_relief)
                    text(label_text,
                         size    = label_size,
                         font    = label_font,
                         halign  = "center",
                         valign  = "center");
        }
    }
}


// =============================================================================
// RENDER — controlled by `render_part`
// =============================================================================
// Default value previews all parts side-by-side in the OpenSCAD GUI.
// Override at the CLI to export a single part for slicer import:
//
//   openscad -o aiprime-case-bottom.stl  -D 'render_part="bottom"'         case.scad
//   openscad -o aiprime-case-lid.stl     -D 'render_part="lid"'            case.scad
//   openscad -o aiprime-case-lid-diff.stl -D 'render_part="lid_diffuser"'  case.scad
//
// Or in the GUI: change the value below temporarily, F6 to render, File → Export.

render_part = "preview";   // "preview" | "bottom" | "lid" | "lid_diffuser"

if (render_part == "bottom") {
    case_bottom();
} else if (render_part == "lid") {
    // Print orientation: lid prints lip-DOWN on the build plate. The .stl is
    // exported as-modeled (lip pointing -Z), so the slicer will see it in the
    // correct print orientation by default. No rotation needed.
    case_lid();
} else if (render_part == "lid_diffuser") {
    case_lid_with_diffuser_well();
} else {
    // Side-by-side preview only — do NOT export this as a single STL.
    case_bottom();
    translate([case_l + 10, 0, 0])
        rotate([180, 0, 0])
            translate([0, 0, -top_thickness])
                case_lid();
    translate([2 * (case_l + 10), 0, 0])
        rotate([180, 0, 0])
            translate([0, 0, -top_thickness])
                case_lid_with_diffuser_well();
}
