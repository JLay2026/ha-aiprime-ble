"""AquaIllumination .aip (Signature Series) profile parser.

Pure, offline, dependency-free (stdlib only). Parses an AI Prime ".aip"
schedule export (XML <ramp>) into an internal model, with helpers to convert
intensities to the device write scale and to interpolate the ramp at any
minute of day.

Scope (per project vision 2026-06-11): HA is READ-ONLY on .aip - myAI authors
and exports profiles; HA imports and deploys them. So this module parses only;
it does NOT generate .aip or compute the header checksum.

Format (decoded 2026-06-11 from two real samples):
  <ramp>
    <header><version>2</version><checksum>INT</checksum></header>
    <colors>
      <COLOR><point><intensity>I</intensity><time>T</time></point>...</COLOR>
      ...
    </colors>
  </ramp>
  - COLOR in {blue, green, deep_red, moonlight, warm_white, cool_white}.
    A profile MAY omit colors (gregory.aip has 5, no moonlight); omitted
    colors are treated as off (no points).
  - time  T = minutes of day, 0..1440 (e.g. 600 = 10:00, 1200 = 20:00).
  - intensity I = 0..2000, where 2000 = 100%.
      device WRITE scale (attr 407) = round(I / 2)  -> 0..1000
      device READ  scale (attr 1504) = I * 10        -> 0..20000
  See memory [[aiprime-aip-schedule-format]].
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import shutil
import xml.etree.ElementTree as ET

# Full 6-color palette the AI Prime exposes, in a stable canonical order.
# Color -> device channel-ID mapping is NOT decided here (still TBD: empirical
# slider test or deploy capture); this module stays mapping-agnostic and keys
# everything by color name.
AIP_COLORS: tuple[str, ...] = (
    "blue",
    "green",
    "deep_red",
    "moonlight",
    "warm_white",
    "cool_white",
)

INTENSITY_MAX_AIP = 2000          # .aip full-scale (= 100%)
DEVICE_WRITE_MAX = 1000           # attr-407 write full-scale
DEVICE_READ_MAX = 20000           # attr-1504 read full-scale
MINUTES_PER_DAY = 1440


class AipParseError(ValueError):
    """Raised when a .aip file is structurally invalid."""


@dataclass(frozen=True)
class RampPoint:
    """One control point: intensity (native .aip 0..2000) at a minute of day."""

    minute: int
    intensity: int

    @property
    def clock(self) -> str:
        h, m = divmod(self.minute % MINUTES_PER_DAY, 60)
        return f"{h:02d}:{m:02d}"

    @property
    def percent(self) -> float:
        return self.intensity / INTENSITY_MAX_AIP * 100.0

    @property
    def device_write(self) -> int:
        return intensity_to_device_write(self.intensity)


@dataclass
class Profile:
    """A parsed .aip ramp: per-color point lists plus header metadata."""

    name: str
    version: int | None = None
    checksum: int | None = None
    colors: dict[str, list[RampPoint]] = field(default_factory=dict)

    def points(self, color: str) -> list[RampPoint]:
        """Points for a color (sorted by minute); [] if the profile omits it."""
        return self.colors.get(color, [])

    def present_colors(self) -> list[str]:
        """Colors actually present in this profile, in canonical order."""
        return [c for c in AIP_COLORS if self.colors.get(c)]

    def value_at(self, color: str, minute: int) -> float:
        """Interpolated native intensity (0..2000) for a color at a minute.

        Linear interpolation with circular (24 h) wrap between the last point
        and the first. Omitted color -> 0.0.
        """
        return _interpolate(self.points(color), minute)

    def device_write_at(self, color: str, minute: int) -> int:
        """Interpolated value converted to the attr-407 write scale (0..1000)."""
        return intensity_to_device_write(round(self.value_at(color, minute)))


def intensity_to_device_write(intensity: int) -> int:
    """Convert a native .aip intensity (0..2000) to attr-407 write units."""
    if intensity <= 0:
        return 0
    if intensity >= INTENSITY_MAX_AIP:
        return DEVICE_WRITE_MAX
    return round(intensity * DEVICE_WRITE_MAX / INTENSITY_MAX_AIP)


def intensity_to_percent(intensity: int) -> float:
    return max(0.0, min(100.0, intensity / INTENSITY_MAX_AIP * 100.0))


def _interpolate(points: list[RampPoint], minute: int) -> float:
    if not points:
        return 0.0
    pts = sorted(points, key=lambda p: p.minute)
    if len(pts) == 1:
        return float(pts[0].intensity)
    minute %= MINUTES_PER_DAY
    # Extend with circular sentinels so any minute falls inside a bracket.
    ext: list[tuple[int, int]] = (
        [(pts[-1].minute - MINUTES_PER_DAY, pts[-1].intensity)]
        + [(p.minute, p.intensity) for p in pts]
        + [(pts[0].minute + MINUTES_PER_DAY, pts[0].intensity)]
    )
    for (t0, v0), (t1, v1) in zip(ext, ext[1:]):
        if t0 <= minute <= t1:
            if t1 == t0:
                return float(v0)
            frac = (minute - t0) / (t1 - t0)
            return v0 + (v1 - v0) * frac
    return float(pts[-1].intensity)  # unreachable in practice


def _int_text(parent: ET.Element, tag: str) -> int:
    el = parent.find(tag)
    if el is None or el.text is None or el.text.strip() == "":
        raise AipParseError(f"missing/empty <{tag}>")
    try:
        return int(el.text.strip())
    except ValueError as err:
        raise AipParseError(f"<{tag}> not an integer: {el.text!r}") from err


def parse_aip(source: str | Path, *, name: str | None = None) -> Profile:
    """Parse a .aip file path or raw XML string into a Profile.

    `name` defaults to the file stem when a path is given, else "profile".
    Raises AipParseError on structural problems.
    """
    raw: str
    if isinstance(source, Path) or (
        isinstance(source, str) and not source.lstrip().startswith("<")
    ):
        path = Path(source)
        raw = path.read_text(encoding="utf-8")
        if name is None:
            name = path.stem
    else:
        raw = source
        if name is None:
            name = "profile"

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise AipParseError(f"invalid XML: {err}") from err

    if root.tag != "ramp":
        raise AipParseError(f"root element is <{root.tag}>, expected <ramp>")

    version: int | None = None
    checksum: int | None = None
    header = root.find("header")
    if header is not None:
        v = header.find("version")
        c = header.find("checksum")
        if v is not None and v.text and v.text.strip():
            version = int(v.text.strip())
        if c is not None and c.text and c.text.strip():
            checksum = int(c.text.strip())

    colors_el = root.find("colors")
    if colors_el is None:
        raise AipParseError("missing <colors> element")

    colors: dict[str, list[RampPoint]] = {}
    for color_el in colors_el:
        color = color_el.tag
        pts: list[RampPoint] = []
        for point_el in color_el.findall("point"):
            intensity = _int_text(point_el, "intensity")
            minute = _int_text(point_el, "time")
            pts.append(RampPoint(minute=minute, intensity=intensity))
        pts.sort(key=lambda p: p.minute)
        colors[color] = pts
        if color not in AIP_COLORS:
            # Don't fail - just surface an unexpected color so we notice a
            # 7th channel or a renamed tag on some other AI model.
            colors.setdefault("_unknown", [])  # marker; see present_colors()

    return Profile(name=name, version=version, checksum=checksum, colors=colors)


def save_profile_file(
    src_path: str | Path,
    name: str | None,
    dest_dir: str | Path,
) -> str:
    """Validate an .aip file and copy it into the profiles directory.

    Pure/offline (stdlib only) so it can be unit-tested without Home Assistant.
    Used by the config-flow .aip importer after HA's file_upload hands over the
    uploaded temp path.

    - Validates by parsing (raises :class:`AipParseError` on a non-.aip file).
    - Destination filename is `name` (if given) else the source filename;
      basename-only (path-traversal guard); `.aip` extension ensured.
    - Creates `dest_dir` if missing.

    Returns the saved filename (e.g. ``"my-profile.aip"``).
    """
    src_path = Path(src_path)
    parse_aip(src_path)  # validate; raises AipParseError on invalid content

    base = (name or "").strip() or src_path.name
    base = os.path.basename(base)  # strip any directory components (traversal)
    if not base.lower().endswith(".aip"):
        base += ".aip"
    if base in ("", ".aip"):
        raise AipParseError("empty profile name")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / base
    shutil.copyfile(src_path, dest)
    return base
