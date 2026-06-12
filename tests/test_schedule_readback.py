"""Offline tests for the PR-6 schedule read-back decode + profile match, and
the deploy framing path.

The decode/match pipeline was validated this session against a REAL attr-500
GET CONFIRM captured from a myAI session (which reflected the 'AI Preset'
schedule); to keep the committed test self-contained we re-encode AI Preset's
points into schedule items here rather than ship the ~750-byte raw frame.

Loads only the HA-free protocol modules, so it runs with no Home Assistant.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "aiprime_ble"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Device channel order inside a 24-byte schedule point (matches
# const.CHANNEL_WRITE_ORDER); used only to synthesize read items in-test.
_ORDER = (0x11, 0x13, 0x19, 0x1E, 0x16, 0x10, 0x01)


def _load_pure_modules():
    for name in ("ap", "ap.protocol"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = []
            mod.__package__ = name
            sys.modules[name] = mod

    def _load(modname: str, path: Path):
        spec = importlib.util.spec_from_file_location(modname, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)
        return module

    _load("ap.const", PKG / "const.py")
    aip = _load("ap.protocol.aip", PKG / "protocol" / "aip.py")
    _load("ap.protocol.fsci", PKG / "protocol" / "fsci.py")
    schedule = _load("ap.protocol.schedule", PKG / "protocol" / "schedule.py")
    return schedule, aip


schedule, aip = _load_pure_modules()


def _profiles() -> dict:
    return {
        "ai-preset": aip.parse_aip(FIXTURES / "ai-preset.aip"),
        "ai-signature-gregory": aip.parse_aip(FIXTURES / "ai-signature-gregory.aip"),
    }


def _encode_items(points) -> list[bytes]:
    """Encode (minute, flag, chans) tuples into 24-byte device schedule items."""
    items = []
    for minute, flag, chans in points:
        b = struct.pack("<HB", minute, flag)
        for cid in _ORDER:
            b += bytes([cid]) + struct.pack("<H", chans[cid])
        items.append(b)
    return items


def _deploy_frames() -> dict[int, bytes]:
    frames: dict[int, bytes] = {}
    for line in (FIXTURES / "deploy_gregory_frames.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tag, hexstr = line.split()
        frames[int(tag.split("=")[1])] = bytes.fromhex(hexstr)
    return frames


def test_parse_schedule_read_drops_terminator_and_matches():
    profiles = _profiles()
    expected = schedule.profile_to_points(profiles["ai-preset"])
    # device read = active points then an all-zero terminator + capacity pad.
    items = _encode_items(expected) + [bytes(24), bytes(24)]
    decoded = schedule.parse_schedule_read(items)
    assert len(decoded) == len(expected), (len(decoded), len(expected))
    assert schedule.match_active_profile(decoded, profiles) == "ai-preset"


def test_roundtrip_profile_matches_itself():
    profiles = _profiles()
    for name, prof in profiles.items():
        pts = schedule.profile_to_points(prof)
        assert schedule.match_active_profile(pts, profiles) == name


def test_no_match_returns_none():
    assert schedule.match_active_profile([], _profiles()) is None


def test_public_builders_reproduce_capture():
    """The deploy path frames via build_schedule_set_frame / build_commit_frame
    (codec allocates the msg_id). Verify byte-for-byte vs the captured deploy."""
    captured = _deploy_frames()
    profile = aip.parse_aip(FIXTURES / "ai-signature-gregory.aip")
    points = schedule.profile_to_points(profile)
    assert schedule.build_schedule_set_frame(points, 8) == captured[8]
    for mid, value in zip((9, 10), schedule.COMMIT_VALUES):
        assert schedule.build_commit_frame(value, mid) == captured[mid]


def main() -> int:
    tests = [
        ("parse_schedule_read drops terminator + matches ai-preset",
         test_parse_schedule_read_drops_terminator_and_matches),
        ("round-trip profile matches itself", test_roundtrip_profile_matches_itself),
        ("empty schedule -> no match", test_no_match_returns_none),
        ("public builders reproduce captured deploy",
         test_public_builders_reproduce_capture),
    ]
    ok = True
    for label, fn in tests:
        try:
            fn()
            print(f"PASS  {label}")
        except AssertionError as err:
            ok = False
            print(f"FAIL  {label}: {err}")
    print("\nREADBACK GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
