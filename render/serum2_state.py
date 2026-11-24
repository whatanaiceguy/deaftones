import hashlib
import json
import re
import struct

import cbor2
import zstandard as zstd

from juce_state import b64_decode, b64_encode

MAGIC = b"XferJson\x00"
FILE_ONLY = ("fileType",)


def cbor(o, out=None):
    top = out is None
    out = [] if top else out

    def head(major, n):
        if n < 24:
            out.append(bytes([major << 5 | n]))
        elif n < 256:
            out.append(bytes([major << 5 | 24, n]))
        elif n < 65536:
            out.append(bytes([major << 5 | 25]) + struct.pack(">H", n))
        elif n < 2 ** 32:
            out.append(bytes([major << 5 | 26]) + struct.pack(">I", n))
        else:
            out.append(bytes([major << 5 | 27]) + struct.pack(">Q", n))

    if o is None:
        out.append(b"\xf6")
    elif o is True:
        out.append(b"\xf5")
    elif o is False:
        out.append(b"\xf4")
    elif isinstance(o, int):
        head(0, o) if o >= 0 else head(1, -o - 1)
    elif isinstance(o, float):
        f = struct.pack(">f", o)
        out.append(b"\xfa" + f if struct.unpack(">f", f)[0] == o or o != o else b"\xfb" + struct.pack(">d", o))
    elif isinstance(o, str):
        b = o.encode("utf-8")
        head(3, len(b))
        out.append(b)
    elif isinstance(o, bytes):
        head(2, len(o))
        out.append(o)
    elif isinstance(o, list):
        head(4, len(o))
        for x in o:
            cbor(x, out)
    elif isinstance(o, dict):
        head(5, len(o))
        for k, v in o.items():
            cbor(k, out)
            cbor(v, out)
    else:
        raise TypeError(f"cbor: {type(o)}")
    return b"".join(out) if top else None


def unxfer(raw):
    assert raw.startswith(MAGIC), "not XferJson"
    n, _ = struct.unpack_from("<II", raw, 9)
    head = json.loads(raw[17:17 + n])
    off = 17 + n
    clen, _ = struct.unpack_from("<II", raw, off)
    body = zstd.ZstdDecompressor().decompress(raw[off + 8:])
    assert len(body) == clen, "cbor length"
    return head, cbor2.loads(body)


def xfer(head, body):
    c = cbor(body)
    z = zstd.ZstdCompressor(level=3).compress(c)
    m = json.dumps({**head, "hash": hashlib.md5(z).hexdigest()}, separators=(",", ":")).encode()
    return MAGIC + struct.pack("<II", len(m), 0) + m + struct.pack("<II", len(c), 2) + z


def read_preset(path):
    with open(path, "rb") as f:
        return unxfer(f.read())


def parse_state(state):
    xml = state[8:].decode("utf-8").rstrip("\x00")
    part = lambda tag: unxfer(b64_decode(re.search(rf"<{tag}>(.*?)</{tag}>", xml).group(1)))
    return part("IComponent"), part("IEditController")


def build_state(proc, ctrl):
    xml = ('<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>'
           + b64_encode(xfer(*proc)) + "</IComponent><IEditController>"
           + b64_encode(xfer(*ctrl)) + "</IEditController></VST3PluginState>").encode() + b"\x00"
    return b"VC2!" + struct.pack("<I", len(xml) - 1) + xml


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [merge(x, y) for x, y in zip(a, b)]
    return b if a is None else a


class Template:
    def __init__(self, fresh_state):
        (self.ph, self.pb), (self.ch, self.cb) = parse_state(fresh_state)
        assert self.pb.get("component") == "processor" and self.cb.get("component") == "controller"

    def state(self, data, ctrl_from_file=True):
        proc = {k: (data[k] if k in data else v) for k, v in self.pb.items()}
        proc.update({k: v for k, v in data.items() if k not in self.pb and k not in self.cb and k not in FILE_ONLY})
        proc["component"] = "processor"
        ctrl = dict(self.cb)
        if ctrl_from_file:
            for k, v in self.cb.items():
                if k in data and k != "component":
                    ctrl[k] = data[k] if data[k] is not None else v
            for k, v in data.items():
                if k.startswith("Macro") and isinstance(v, dict) and "name" in v:
                    pp = v.get("plainParams")
                    val = pp.get("kParamValue", 0.0) if isinstance(pp, dict) else 0.0
                    ctrl[k] = {"default": val / 100, "name": v["name"]}
        return build_state((self.ph, proc), (self.ch, ctrl))

    def preset(self, state):
        (_, pb), (_, cb) = parse_state(state)
        return merge(pb, cb)
