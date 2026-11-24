"""Vital's VST3 plugin state, built from a .vital file, for DawDreamer's load_state.

The plugin's own state (save_state of a fresh Vital.vst3) is:

  "VC2!" + uint32 LE size of the XML (without its trailing null) + XML + null
  XML: <VST3PluginState><IComponent>N.base64</IComponent></VST3PluginState>
  base64 is JUCE's MemoryBlock::toBase64Encoding: N = byte count, then 6 bits
  per char, least significant first, alphabet .A-Za-z0-9+
  decoded: a VST2-style bank, "VstW" header (16 bytes), "CcnK" + size (BE, the
  rest after it) + "FBCh" ... "Vita" ..., chunk size (BE) at byte 172, chunk at 176
  chunk: the preset JSON (what Vital also writes to a .vital) + null + JUCE's
  private data, kept as the plugin wrote it

Rebuilding the fresh state from its own JSON gives it back byte for byte
(checked by template()).
"""
import re
import struct

import numpy as np

CH = np.frombuffer(b".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+", dtype=np.uint8)
LUT = np.zeros(256, dtype=np.uint8)
LUT[CH] = np.arange(64)
W = (1 << np.arange(6)).astype(np.uint8)


def b64_encode(b: bytes) -> str:
    bits = np.unpackbits(np.frombuffer(b, dtype=np.uint8), bitorder="little")
    bits = np.concatenate([bits, np.zeros((-len(bits)) % 6, dtype=np.uint8)])
    return f"{len(b)}." + CH[bits.reshape(-1, 6) @ W].tobytes().decode()


def b64_decode(s: str) -> bytes:
    n, body = s.split(".", 1)
    v = LUT[np.frombuffer(body.encode(), dtype=np.uint8)]
    bits = ((v[:, None] >> np.arange(6)) & 1).astype(np.uint8).ravel()[: int(n) * 8]
    return np.packbits(bits, bitorder="little").tobytes()


class Template:
    def __init__(self, fresh_state: bytes):
        xml = fresh_state[8:].decode("utf-8").rstrip("\x00")
        raw = b64_decode(re.search(r"<IComponent>(.*)</IComponent>", xml).group(1))
        assert raw[:4] == b"VstW" and raw[16:20] == b"CcnK" and raw[24:28] == b"FBCh" and raw[32:36] == b"Vita"
        size = struct.unpack(">I", raw[172:176])[0]
        chunk = raw[176:176 + size]
        end = chunk.index(b"\x00")
        self.head, self.trailer = raw[:176], chunk[end:]
        assert self.state(chunk[:end]) == fresh_state, "state layout differs from the one this was written for"

    def state(self, preset_json: bytes) -> bytes:
        chunk = preset_json.strip() + self.trailer
        raw = bytearray(self.head) + chunk
        raw[172:176] = struct.pack(">I", len(chunk))
        raw[20:24] = struct.pack(">I", len(raw) - 24)
        xml = ('<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>'
               + b64_encode(bytes(raw)) + "</IComponent></VST3PluginState>").encode() + b"\x00"
        return b"VC2!" + struct.pack("<I", len(xml) - 1) + xml
