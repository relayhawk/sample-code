"""
Generate sip_dialer/audio/test-call.wav (macOS only).

Requires: macOS `say` and `afconvert` (both ship with macOS).

Usage:
    python3 sip_dialer/make-audio.py
"""
import os, struct, subprocess, sys, tempfile
from pathlib import Path

OUT = Path(__file__).parent / "audio" / "test-call.wav"
TEXT = "Hello. This is a test call from the Free World Dialup vCons demo. Goodbye."

with tempfile.TemporaryDirectory() as tmp:
    aiff = os.path.join(tmp, "raw.aiff")
    wav  = os.path.join(tmp, "raw.wav")

    print("Generating speech...")
    subprocess.run(["say", "-v", "Samantha", TEXT, "-o", aiff], check=True)

    print("Converting to 48 kHz stereo PCM WAV...")
    subprocess.run(
        ["afconvert", aiff, "-o", wav, "-f", "WAVE", "-d", "LEI16@48000", "-c", "2"],
        check=True,
    )

    # Strip non-standard chunks (FLLR, JUNK, etc.) that baresip rejects
    with open(wav, "rb") as f:
        data = f.read()

    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    pos, fmt_chunk, data_chunk = 12, None, None
    while pos < len(data):
        cid = data[pos:pos+4]
        csz = struct.unpack_from("<I", data, pos+4)[0]
        cdat = data[pos+8:pos+8+csz]
        if cid == b"fmt ":  fmt_chunk  = (csz, cdat)
        elif cid == b"data": data_chunk = (csz, cdat)
        pos += 8 + csz + (csz % 2)

    if not fmt_chunk or not data_chunk:
        print("ERROR: could not find fmt or data chunk", file=sys.stderr)
        sys.exit(1)

    total = 4 + 8 + fmt_chunk[0] + 8 + data_chunk[0]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(b"RIFF"); f.write(struct.pack("<I", total)); f.write(b"WAVE")
        f.write(b"fmt "); f.write(struct.pack("<I", fmt_chunk[0])); f.write(fmt_chunk[1])
        f.write(b"data"); f.write(struct.pack("<I", data_chunk[0])); f.write(data_chunk[1])

print(f"Written: {OUT} ({total+8:,} bytes, 48 kHz stereo PCM)")
