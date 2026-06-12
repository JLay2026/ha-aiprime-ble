# tools/ - reverse-engineering & verification helpers

Offline, dependency-free helpers used to decode and verify the AI Prime BLE
wire protocol. Not imported by the integration at runtime.

## pklg.py - PacketLogger -> ATT -> FSCI decoder
Decodes an iOS PacketLogger (`.pklg`) BLE capture into CRC-verified FSCI frames.
Frame detection is anchored on the FSCI CRC16, so it reassembles frames split
across multiple BLE writes (CHAR_TX_DATA + CHAR_TX_FINAL) without guessing.

```
python3 tools/pklg.py <capture.pklg> --dir sent --set-only        # all SET frames
python3 tools/pklg.py <capture.pklg> --set-only --attr 500        # schedule writes
python3 tools/pklg.py <capture.pklg> --gatt                       # handle->UUID map
```

## Regenerating the deploy test fixture
`tests/fixtures/deploy_gregory_frames.txt` is the CRC-verified schedule-deploy
sequence (msgid 8/9/10) extracted from a capture of a real myAI deploy of
`ai-signature-gregory.aip`. Captures themselves are NOT committed (large; may
carry unrelated BLE traffic) - see `.gitignore`. Keep captures locally and
re-run `pklg.py` to regenerate the fixture if needed.

## The verification gate
`tests/test_schedule_deploy.py` reconstructs the deploy from the .aip and
byte-compares (incl. CRC) against the captured fixture:

```
python3 tests/test_schedule_deploy.py    # -> "GATE: PASS"
```
