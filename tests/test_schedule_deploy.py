"""Offline byte-compare gate for the .aip schedule-deploy codec.

Reconstructs the captured myAI deploy (PacketLogger capture, msgid 8/9/10) from
the gregory .aip profile and asserts the generated FSCI frames match the capture
byte-for-byte, CRC included. THIS IS THE VERIFICATION GATE: no live deploy is
trusted until this passes.

Runs standalone (`python3 tests/test_schedule_deploy.py`) and under pytest.
Loads only the HA-free protocol modules (const/fsci/aip/schedule) via a
synthetic package, so it needs no Home Assistant install.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "custom_components" / "aiprime_ble"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_pure_modules():
    """Load const/fsci/aip/schedule without triggering the HA-importing
    package __init__ files."""
    for name in ("ap", "ap.protocol"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = []          # mark as package
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


def _load_captured_frames() -> dict[int, bytes]:
    frames: dict[int, bytes] = {}
    for line in (FIXTURES / "deploy_gregory_frames.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tag, hexstr = line.split()
        frames[int(tag.split("=")[1])] = bytes.fromhex(hexstr)
    return frames


def _first_diff(a: bytes, b: bytes) -> str:
    n = min(len(a), b and len(b) or 0)
    for i in range(n):
        if a[i] != b[i]:
            lo, hi = max(0, i - 4), i + 5
            return (f"first diff at byte {i}: expected 0x{a[i]:02x} got 0x{b[i]:02x}\n"
                    f"   exp[{lo}:{hi}]={a[lo:hi].hex()}  got[{lo}:{hi}]={b[lo:hi].hex()}")
    if len(a) != len(b):
        return f"length differs: expected {len(a)} got {len(b)}"
    return "identical"


def build_generated_frames() -> dict[int, bytes]:
    profile = aip.parse_aip(FIXTURES / "ai-signature-gregory.aip")
    return {mid: frame for mid, frame in schedule.build_deploy_sequence(profile, 8)}


def test_schedule_deploy_byte_exact():
    expected = _load_captured_frames()
    generated = build_generated_frames()
    assert set(generated) == set(expected), (set(generated), set(expected))
    for mid in sorted(expected):
        assert generated[mid] == expected[mid], (
            f"msgid {mid} mismatch: {_first_diff(expected[mid], generated[mid])}"
        )


def main() -> int:
    expected = _load_captured_frames()
    generated = build_generated_frames()
    ok = True
    for mid in sorted(expected):
        exp = expected[mid]
        got = generated.get(mid, b"")
        match = exp == got
        ok = ok and match
        print(f"msgid {mid:2}: {'PASS' if match else 'FAIL'}  "
              f"(captured {len(exp)}B, generated {len(got)}B)")
        if not match:
            print("   " + _first_diff(exp, got))
            print(f"   captured : {exp.hex()}")
            print(f"   generated: {got.hex()}")
    print("\nGATE:", "PASS - generated deploy matches capture byte-for-byte"
          if ok else "FAIL - see diffs above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
