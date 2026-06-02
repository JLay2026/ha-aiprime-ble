// =============================================================================
// ESP32-S3 BT Proxy enclosure for the Lonely Binary N16R8 Gold Edition (IPEX)
//
// Two-piece M3-screwed case sized for an under-cabinet / wall-shelf install
// next to the Waterbox25 fishtank. Designed to print on a Bambu Lab X1C.
//
// v0.6 (2026-06-01) — consolidates iterations v0.2 → v0.6 from physical fit
// tests. See hardware/CHANGELOG.md for the version-by-version evolution.
//   - Asymmetric end-gaps (18 mm antenna / 0.5 mm USB-C)
//   - Antenna-end posts serve as PCB X-stop, flank SMA bulkhead
//   - USB-end posts in side-gaps near USB wall (v0.3 style)
//   - M3 brass heat-set inserts (4.0 mm pilot)
//   - Standoffs halved to 2.5 mm (top-mounted ports only)
//   - Flat lid (no drop-in lip) — sits on top of bottom shell
// =============================================================================


// ---- PCB ---------------------------------------------------------------------
pcb_length          = 62.66;
pcb_width           = 25.67;
pcb_thickness       = 1.6;
pcb_xy_clearance    = 0.6;

// Asymmetric end-gaps
pcb_end_gap_antenna = 18.0;   // mm — antenna-end gap houses bulkhead workspace
pcb_end_gap_usbc    = 0.5;    // mm — USB-end gap is thermal/print tolerance only
pcb_side_gap        = 5.0;    // mm — side-gaps house the USB-end corner posts


// ---- USB-C cutout ------------------------------------------------------------
// Both ports confirmed top-mounted side-by-side.
usbc_cutout_w       = 23.4;   // lands ~23 mm post-PLA-shrinkage
usbc_cutout_h       = 6.0;    // top-mounted port body + cable flange margin
usbc_cutout_y_nudge = 0;


// ---- SMA antenna bulkhead — TOP face on the LID (disabled by default) -------
sma_top_enabled     = false;
sma_hole_d          = 6.5;
sma_hole_offset_x   = 18;


// ---- SMA antenna bulkhead — END passthrough on the BOTTOM shell -------------
// Mounts through the antenna-end SHORT wall (-X). Antenna sticks out the end.
// Bulkhead barrel = 6.15 mm; hole = 6.30 mm (0.15 mm slop).
sma_side_enabled        = true;
sma_side_d              = 6.30;
sma_side_y_offset       = 0;
sma_side_z              = 9.0;
// Bulkhead hex-nut effective body radius. Used to keep flanking screw posts
// clear of the nut for wrench access.
bulkhead_body_radius    = 4.5;


// ---- Status LED window -------------------------------------------------------
led_window_d        = 3.2;
led_window_offset_x = 18;


// ---- Walls and corners -------------------------------------------------------
wall                = 2.4;
top_thickness       = 2.0;
bottom_thickness    = 2.0;
corner_r            = 2.5;


// ---- Internal vertical clearances --------------------------------------------
above_pcb           = 7.5;
below_pcb           = 2.5;    // halved from 5.0 since no bottom-mounted port


// ---- Lid fit (kept for reference; v0.6 lid is flat, no lip) -----------------
lid_lip_h           = 3.0;
lid_lip_clearance   = 0.4;


// ---- Screw posts (M3 brass heat-set inserts) --------------------------------
post_outer_d        = 5.6;
post_pilot_d        = 4.0;    // M3 heat-set insert OD. Use 2.7 for self-tap fallback.
post_inset_x        = 8.0;    // USB-end posts: distance from PCB short edge inward

// v0.5b antenna-end post positioning:
//   X: post body's PCB-facing edge touches PCB end (= pcb_x - post_outer_d/2).
//   Y: clear of bulkhead hex-nut by this margin on each side.
antenna_post_nut_clearance = 2.0;


// ---- Wall-mount keyholes (disabled by default) ------------------------------
keyhole_enabled     = false;
keyhole_spacing     = 38;
keyhole_head_d      = 7.5;
keyhole_shaft_d     = 3.5;
keyhole_drop        = 6;


// ---- Ventilation slots (UNDERSIDE) -------------------------------------------
vent_count          = 5;
vent_slot_length    = 22;
vent_slot_width     = 1.6;
vent_slot_spacing   = 3.6;


// ---- Strain-relief post ------------------------------------------------------
strain_relief_enabled = true;


// ---- Top-face label (raised relief) ------------------------------------------
label_enabled = true;
label_text    = "BLE PROXY";
label_size    = 4.5;
label_relief  = 1.0;
label_font    = "Liberation Sans:style=Bold";


// =============================================================================
// DERIVED — don't edit below unless you know what you're doing
// =============================================================================

inner_l = pcb_length + 2 * pcb_xy_clearance + pcb_end_gap_antenna + pcb_end_gap_usbc;
inner_w = pcb_width  + 2 * pcb_xy_clearance + 2 * pcb_side_gap;
case_l  = inner_l + 2 * wall;
case_w  = inner_w + 2 * wall;

inner_h_bottom = below_pcb + pcb_thickness + above_pcb;
bottom_h       = inner_h_bottom + bottom_thickness;
lid_h          = top_thickness;   // v0.6: flat lid, no lip

pcb_x = wall + pcb_end_gap_antenna + pcb_xy_clearance;
pcb_y = wall + pcb_side_gap        + pcb_xy_clearance;
pcb_z = bottom_thickness + below_pcb;

// --- Post positions (v0.5b — explicit list of 4 distinct positions) ----------
antenna_post_x = pcb_x - post_outer_d / 2;
antenna_post_y_offset = bulkhead_body_radius + post_outer_d / 2 + antenna_post_nut_clearance;
antenna_post_y_lower  = case_w / 2 - antenna_post_y_offset;
antenna_post_y_upper  = case_w / 2 + antenna_post_y_offset;

usbc_post_x       = pcb_x + pcb_length - post_inset_x;
usbc_post_y_lower = wall + pcb_side_gap / 2;
usbc_post_y_upper = case_w - wall - pcb_side_gap / 2;

post_positions = [
    [antenna_post_x, antenna_post_y_lower],
    [antenna_post_x, antenna_post_y_upper],
    [usbc_post_x,    usbc_post_y_lower],
    [usbc_post_x,    usbc_post_y_upper],
];


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
        rounded_box(case_l, case_w, bottom_h, corner_r);

        translate([wall, wall, bottom_thickness])
            rounded_pocket(inner_l, inner_w, inner_h_bottom + 1, corner_r - wall);

        // USB-C cutout
        translate([
            case_l - wall - 0.1,
            (case_w - usbc_cutout_w) / 2 + usbc_cutout_y_nudge,
            pcb_z + pcb_thickness - 1.5
        ])
            cube([wall + 0.2, usbc_cutout_w, usbc_cutout_h]);

        // ventilation slots
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

        // SMA bulkhead end passthrough
        if (sma_side_enabled) {
            translate([-0.1,
                       case_w / 2 + sma_side_y_offset,
                       sma_side_z])
                rotate([0, 90, 0])
                    cylinder(r = sma_side_d / 2, h = wall + 0.2, $fn = 48);
        }

        // wall-mount keyholes (disabled by default)
        if (keyhole_enabled) {
            kh_z = bottom_h * 0.55;
            for (dx = [-keyhole_spacing / 2, keyhole_spacing / 2]) {
                translate([case_l / 2 + dx, case_w - wall - 0.05, kh_z]) {
                    rotate([-90, 0, 0]) {
                        cylinder(r = keyhole_head_d / 2, h = wall + 0.2, $fn = 48);
                        translate([-keyhole_shaft_d / 2, -keyhole_drop, 0])
                            cube([keyhole_shaft_d, keyhole_drop, wall + 0.2]);
                    }
                }
            }
        }
    }

    // 4x PCB-rest standoffs at PCB corners (2.5 mm tall in v0.4+)
    standoff_inset = 4;
    for (sx = [pcb_x + standoff_inset, pcb_x + pcb_length - standoff_inset],
         sy = [pcb_y + standoff_inset, pcb_y + pcb_width  - standoff_inset])
    {
        translate([sx, sy, bottom_thickness])
            cylinder(r = 1.5, h = below_pcb, $fn = 24);
    }

    // 4x screw posts at explicit positions
    for (p = post_positions) {
        translate([p[0], p[1], bottom_thickness])
            difference() {
                cylinder(r = post_outer_d / 2, h = inner_h_bottom, $fn = 32);
                translate([0, 0, -0.1])
                    cylinder(r = post_pilot_d / 2, h = inner_h_bottom + 0.2, $fn = 24);
            }
    }

    // Strain-relief post near USB-C cutout
    if (strain_relief_enabled) {
        sr_x = case_l - wall - 6;
        sr_y = wall + 4;
        translate([sr_x, sr_y, bottom_thickness])
            cylinder(r = 1.8, h = inner_h_bottom * 0.45, $fn = 24);
    }
}


// =============================================================================
// LID — v0.6 flat (no drop-in lip)
// =============================================================================

module case_lid() {
    union() {
        difference() {
            // Flat top plate only
            rounded_box(case_l, case_w, top_thickness, corner_r);

            // SMA hole on top face — disabled by default
            if (sma_top_enabled) {
                translate([sma_hole_offset_x, case_w / 2, -0.5])
                    cylinder(r = sma_hole_d / 2, h = top_thickness + 1, $fn = 48);
            }

            // Status LED window
            translate([case_l - led_window_offset_x, case_w / 2, -0.5])
                cylinder(r = led_window_d / 2, h = top_thickness + 1, $fn = 32);

            // 4x M3 through-holes + shallow counter-bores for socket-cap heads
            for (p = post_positions) {
                translate([p[0], p[1], -0.5])
                    cylinder(r = 1.7, h = top_thickness + 1, $fn = 24);
                translate([p[0], p[1], top_thickness - 1.0])
                    cylinder(r = 3.0, h = 1.0 + 0.1, $fn = 32);
            }
        }

        // Raised "BLE PROXY" label
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

// Diffuser variant currently identical to default lid — well-recess TBD when
// the basic lid is print-verified.
module case_lid_with_diffuser_well() {
    case_lid();
}


// =============================================================================
// RENDER — controlled by `render_part`
// =============================================================================
// GUI: F6 with default "preview" shows both side-by-side.
// CLI export:
//   openscad -o aiprime-case-bottom.stl -D 'render_part="bottom"' case.scad
//   openscad -o aiprime-case-lid.stl    -D 'render_part="lid"'    case.scad

render_part = "preview";

if (render_part == "bottom") {
    case_bottom();
} else if (render_part == "lid") {
    case_lid();
} else if (render_part == "lid_diffuser") {
    case_lid_with_diffuser_well();
} else {
    case_bottom();
    translate([case_l + 10, 0, 0]) case_lid();
}
