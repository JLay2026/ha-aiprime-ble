"""FSCI codec — placeholder.

To be filled in Day 3 by lifting
https://github.com/mpshevlotsky/ai-pump-feed-esp32/blob/main/core/protocol/fsci.py
into this module, with MicroPython-specific shims removed and AI-Prime
attribute IDs from the Mobius Settings Dump bolted on.

Validation test (Day 3, ~5 min):
  1. Connect via bleak to MAC 1C:BC:EC:0A:E2:D0.
  2. Subscribe to CHAR_RX_DATA and CHAR_RX_FINAL.
  3. Build FsciCodec().build_get_attribute(3) — attribute 3 is the serial.
  4. Write the frame to CHAR_TX_DATA.
  5. Concatenate RX notifications until CHAR_RX_FINAL fires.
  6. Expect payload to contain ASCII bytes 41 30 39 46 30 41 45 32 44 30 52 31 43 46
     ("A09F0AE2D0R1CF" — the device serial).
  7. If yes -> framing matches; lift the whole pump module.
  8. If no -> dump GATT tree and capture a Mobius app session via the ESP32 BT
     proxy's sniffer mode to figure out what's different on Qualcomm.
"""

from __future__ import annotations

__all__: list[str] = []
