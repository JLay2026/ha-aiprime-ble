"""Offline tests for the .aip importer's validate+write helper
(protocol.aip.save_profile_file). HA-free — no Home Assistant needed.

The config-flow upload step wires HA's process_uploaded_file to this helper;
that wiring is exercised live, but the validation / naming / write logic (the
part that matters) is covered here.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AIP_PATH = REPO / "custom_components" / "aiprime_ble" / "protocol" / "aip.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_spec = importlib.util.spec_from_file_location("aiprime_aip_standalone", AIP_PATH)
aip = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = aip  # needed so @dataclass can resolve the module
_spec.loader.exec_module(aip)


def test_valid_profile_saved_and_parses():
    with tempfile.TemporaryDirectory() as d:
        saved = aip.save_profile_file(FIXTURES / "ai-signature-gregory.aip", None, d)
        assert saved == "ai-signature-gregory.aip"
        dest = Path(d) / saved
        assert dest.is_file()
        aip.parse_aip(dest)  # round-trips as a valid profile


def test_name_override_adds_extension():
    with tempfile.TemporaryDirectory() as d:
        saved = aip.save_profile_file(FIXTURES / "ai-preset.aip", "My Tank", d)
        assert saved == "My Tank.aip"
        assert (Path(d) / saved).is_file()


def test_path_traversal_is_stripped():
    with tempfile.TemporaryDirectory() as d:
        saved = aip.save_profile_file(FIXTURES / "ai-preset.aip", "../../evil", d)
        assert saved == "evil.aip"
        assert (Path(d) / "evil.aip").is_file()


def test_creates_missing_dest_dir():
    with tempfile.TemporaryDirectory() as d:
        nested = Path(d) / "aiprime" / "profiles"
        saved = aip.save_profile_file(FIXTURES / "ai-preset.aip", None, nested)
        assert (nested / saved).is_file()


def test_invalid_file_rejected():
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.aip"
        bad.write_text("this is not aip xml")
        try:
            aip.save_profile_file(bad, None, d)
        except aip.AipParseError:
            pass
        else:
            raise AssertionError("expected AipParseError for non-.aip content")


def main() -> int:
    tests = [
        ("valid profile saved + parses", test_valid_profile_saved_and_parses),
        ("name override adds .aip", test_name_override_adds_extension),
        ("path traversal stripped", test_path_traversal_is_stripped),
        ("creates missing dest dir", test_creates_missing_dest_dir),
        ("invalid file rejected", test_invalid_file_rejected),
    ]
    ok = True
    for label, fn in tests:
        try:
            fn(); print(f"PASS  {label}")
        except AssertionError as e:
            ok = False; print(f"FAIL  {label}: {e}")
    print("\nIMPORT GATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
