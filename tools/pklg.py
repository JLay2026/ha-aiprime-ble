#!/usr/bin/env python3
"""PacketLogger -> ATT -> FSCI decoder for AI Prime BLE captures.

Durable reverse-engineering tool. Re-derived 2026-06-11 after the original
decoders were lost to a scratch-dir reset. Aligns to the integration's own
FSCI framing (custom_components/aiprime_ble/protocol/fsci.py).

Pipeline:
  1. Walk PacketLogger records ([len:BE][ts_sec:BE][ts_usec:BE][type:1][payload]).
  2. For HCI ACL (type 0x02 sent / 0x03 recv): reassemble L2CAP fragments,
     pull ATT PDUs (Write Cmd 0x52 / Write Req 0x12 / Prepare 0x16 /
     Notification 0x1b / Indication 0x1d), and GATT discovery responses
     (Read-By-Type 0x09, Find-Info 0x05) to map handle -> UUID.
  3. CRC-anchored FSCI scan: concatenate the byte stream per direction and
     walk it; at each offset try to read a full FSCI frame and verify its
     CRC16-CCITT. A passing CRC proves the frame boundary, so fragmentation
     across multiple BLE writes (TX_DATA + TX_FINAL) reassembles for free.

Usage:
  python3 pklg.py <capture.pklg> [--dir sent|recv|both] [--set-only] [--attr N]
                  [--raw-writes] [--gatt]
"""
from __future__ import annotations

import argparse
import struct
import sys

# --- FSCI constants (mirror of repo protocol/fsci.py) ----------------------
STX = 0x02
OP_GROUP = {0xDE: "REQUEST", 0xDF: "CONFIRM"}
OP_CODE = {0x17: "GET", 0x18: "SET"}


def crc16(data: bytes) -> int:
    """CRC16-CCITT poly 0x1021 init 0xFFFF (matches repo fsci.crc16)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else ((crc << 1) & 0xFFFF)
    return crc


# --- PacketLogger record iterator ------------------------------------------
def iter_records(data: bytes):
    i = 0
    n = len(data)
    while i + 4 <= n:
        ln = struct.unpack(">I", data[i:i + 4])[0]
        if ln < 9 or i + 4 + ln > n:
            break
        rec = data[i + 4:i + 4 + ln]
        ts = struct.unpack(">II", rec[0:8])
        typ = rec[8]
        payload = rec[9:]
        yield (ts[0] + ts[1] / 1e6, typ, payload)
        i += 4 + ln


# --- L2CAP reassembly ------------------------------------------------------
class L2CAPReasm:
    """Reassemble L2CAP frames from HCI ACL fragments, per (handle,dir)."""

    def __init__(self):
        self.buf = {}   # key -> bytearray
        self.want = {}  # key -> total expected (4 + l2cap_len)

    def feed(self, key, acl_payload, pb_flag):
        out = []
        if pb_flag == 0x01 and key in self.buf:        # continuation
            self.buf[key] += acl_payload
        else:                                          # start (0x00/0x02)
            self.buf[key] = bytearray(acl_payload)
            self.want[key] = None
        b = self.buf[key]
        if self.want.get(key) is None and len(b) >= 4:
            l2len = struct.unpack("<H", b[0:2])[0]
            self.want[key] = 4 + l2len
        w = self.want.get(key)
        if w is not None and len(b) >= w:
            frame = bytes(b[:w])
            # leftover (rare) starts a new pseudo-frame
            rest = bytes(b[w:])
            del self.buf[key]
            self.want.pop(key, None)
            out.append(frame)
            if rest:
                self.buf[key] = bytearray(rest)
                self.want[key] = None
        return out


def parse_acl(payload):
    if len(payload) < 4:
        return None
    hf = struct.unpack("<H", payload[0:2])[0]
    handle = hf & 0x0FFF
    pb = (hf >> 12) & 0x03
    alen = struct.unpack("<H", payload[2:4])[0]
    return handle, pb, payload[4:4 + alen]


# --- main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--dir", choices=["sent", "recv", "both"], default="sent")
    ap.add_argument("--set-only", action="store_true")
    ap.add_argument("--attr", type=int, default=None, help="filter SET/GET to this attr id")
    ap.add_argument("--raw-writes", action="store_true", help="dump raw ATT write values")
    ap.add_argument("--gatt", action="store_true", help="print handle->UUID map")
    args = ap.parse_args()

    data = open(args.capture, "rb").read()

    reasm = {"sent": L2CAPReasm(), "recv": L2CAPReasm()}
    # per-direction ordered byte stream of ATT *values* (writes for sent;
    # notification values for recv) for CRC-anchored FSCI scan
    streams = {"sent": bytearray(), "recv": bytearray()}
    raw_writes = []        # (ts, handle, opcode, value)
    handle_uuid = {}       # value_handle -> uuid str

    def add_uuid(vhandle, uuid_bytes):
        if len(uuid_bytes) == 2:
            u = f"{struct.unpack('<H', uuid_bytes)[0]:04x}"
        elif len(uuid_bytes) == 16:
            le = uuid_bytes[::-1]
            u = f"{le[0:4].hex()}-{le[4:6].hex()}-{le[6:8].hex()}-{le[8:10].hex()}-{le[10:16].hex()}"
        else:
            u = uuid_bytes.hex()
        handle_uuid[vhandle] = u

    for ts, typ, payload in iter_records(data):
        if typ == 0x02:
            direction = "sent"
        elif typ == 0x03:
            direction = "recv"
        else:
            continue
        acl = parse_acl(payload)
        if not acl:
            continue
        handle, pb, acldata = acl
        for frame in reasm[direction].feed((handle, direction), acldata, pb):
            if len(frame) < 5:
                continue
            l2len = struct.unpack("<H", frame[0:2])[0]
            cid = struct.unpack("<H", frame[2:4])[0]
            if cid != 0x0004:      # ATT only
                continue
            att = frame[4:4 + l2len]
            if not att:
                continue
            op = att[0]
            # GATT discovery (in recv): Read-By-Type Resp 0x09
            if op == 0x09 and len(att) >= 2:
                each = att[1]
                pos = 2
                while pos + each <= len(att):
                    rec = att[pos:pos + each]
                    # char decl (0x2803) value: [props:1][vhandle:2][uuid:2/16]
                    if each in (7, 21):
                        vhandle = struct.unpack("<H", rec[3:5])[0]
                        add_uuid(vhandle, rec[5:])
                    pos += each
            elif op == 0x05 and len(att) >= 2:   # Find Info Resp
                fmt = att[1]
                step = 4 if fmt == 1 else 18
                pos = 2
                while pos + step <= len(att):
                    h = struct.unpack("<H", att[pos:pos + 2])[0]
                    add_uuid(h, att[pos + 2:pos + step])
                    pos += step
            elif op in (0x52, 0x12) and len(att) >= 3:   # Write Cmd/Req
                ah = struct.unpack("<H", att[1:3])[0]
                val = att[3:]
                raw_writes.append((ts, ah, op, val))
                streams["sent"] += val
            elif op == 0x16 and len(att) >= 5:           # Prepare Write Req
                ah = struct.unpack("<H", att[1:3])[0]
                val = att[5:]
                raw_writes.append((ts, ah, op, val))
                streams["sent"] += val
            elif op in (0x1b, 0x1d) and len(att) >= 3:   # Notify / Indicate
                val = att[3:]
                streams["recv"] += val

    if args.gatt:
        print("=== handle -> UUID map ===")
        for h in sorted(handle_uuid):
            print(f"  0x{h:04x}  {handle_uuid[h]}")
        print()

    if args.raw_writes:
        print("=== raw ATT writes (sent) ===")
        for ts, ah, op, val in raw_writes:
            print(f"  t={ts:.3f} h=0x{ah:04x} op=0x{op:02x} len={len(val):3d} {val.hex()}")
        print()

    # --- CRC-anchored FSCI frame scan ---
    dirs = ["sent", "recv"] if args.dir == "both" else [args.dir]
    print(f"=== FSCI frames (CRC-verified) [{', '.join(dirs)}] ===")
    total = 0
    for d in dirs:
        s = bytes(streams[d])
        i = 0
        while i < len(s):
            if s[i] != STX:
                i += 1
                continue
            if i + 9 > len(s):
                break
            plen = struct.unpack("<H", s[i + 7:i + 9])[0]
            flen = 1 + 8 + plen + 2
            if i + flen > len(s):
                i += 1
                continue
            frame = s[i:i + flen]
            inner = frame[1:1 + 8 + plen]
            want = struct.unpack("<H", frame[1 + 8 + plen:flen])[0]
            if crc16(inner) != want:
                i += 1
                continue
            # valid frame
            grp = frame[1]
            opc = frame[2]
            msgid = struct.unpack("<H", frame[3:5])[0]
            payload = frame[9:9 + plen]
            attr = None
            if plen >= 2:
                attr = struct.unpack("<H", payload[0:2])[0]
            if args.set_only and opc != 0x18:
                i += flen
                continue
            if args.attr is not None and attr != args.attr:
                i += flen
                continue
            total += 1
            print(f"[{d}] {OP_GROUP.get(grp,hex(grp))}/{OP_CODE.get(opc,hex(opc))} "
                  f"msgid={msgid} attr={attr} plen={plen}")
            print(f"      frame({flen}): {frame.hex()}")
            print(f"      payload({plen}): {payload.hex()}")
            i += flen
    print(f"\n total FSCI frames matched: {total}")


if __name__ == "__main__":
    main()
