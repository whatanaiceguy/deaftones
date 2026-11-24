import re
from dataclasses import dataclass, field

META_KEYS = ("Bank", "Author", "Usage", "Categories", "Features", "Character")


@dataclass
class H2P:
    meta: list = field(default_factory=list)
    head: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    newline: str = "\n"
    tail: str = ""

    def section(self, name):
        for n, kv in self.sections:
            if n == name:
                return kv
        raise KeyError(name)

    def get(self, sec, key, default=None):
        for k, v in self.section(sec):
            if k == key:
                return v
        return default

    def set(self, sec, key, value):
        kv = self.section(sec)
        for i, (k, _) in enumerate(kv):
            if k == key:
                kv[i] = (k, value)
                return
        raise KeyError((sec, key))

    def values(self):
        return {(n, k): v for n, kv in self.sections for k, v in kv}


def parse(raw: bytes) -> H2P:
    text = raw.decode("latin1")
    body = text.split("$$$$", 1)[0]
    nl = "\r\n" if "\r\n" in body else "\n"
    p = H2P(newline=nl)
    main, sep, after = body.partition("// Section for ugly")
    if sep:
        kept = main.rstrip("\r\n")
        p.tail = main[len(kept):] + sep + after
        body = kept
    else:
        kept = body.rstrip("\r\n")
        p.tail = body[len(kept):]
        body = kept
    rest = body
    if body.lstrip().startswith("/*@Meta"):
        head, rest = body.split("*/", 1)
        p.meta = re.findall(r"^(\w+):\s*\n'((?:\\.|[^'\\])*)'", head.replace("\r\n", "\n"), re.M | re.S)
        rest = rest.lstrip("\r\n")
    cur = None
    for line in rest.replace("\r\n", "\n").split("\n"):
        if not line:
            continue
        if line.startswith("#cm="):
            cur = []
            p.sections.append((line[4:], cur))
        elif cur is None:
            p.head.append(line)
        elif "=" in line:
            k, v = line.split("=", 1)
            cur.append((k, v))
        else:
            raise ValueError(f"unexpected line in section {p.sections[-1][0]}: {line!r}")
    return p


def write(p: H2P, blob: bool = False) -> bytes:
    nl = p.newline
    out = []
    if p.meta:
        out.append("/*@Meta")
        out.append("")
        for k, v in p.meta:
            out += [f"{k}:", f"'{v}'", ""]
        out.append("*/")
        out.append("")
    out += p.head
    for name, kv in p.sections:
        out.append(f"#cm={name}")
        out += [f"{k}={v}" for k, v in kv]
    return (nl.join(out) + p.tail).encode("latin1")
