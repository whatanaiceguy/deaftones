import math
import re

FA, FB = 8.205018, 7.896988

KINDS = {
    "gain_db":   (lambda r: 20 * math.log10(r) if r > 0 else -math.inf,
                  lambda d: 0.0 if d == -math.inf else 10 ** (d / 20), "dB"),
    "main_db":   (lambda r: 20 * math.log10(r) + 3 if r > 0 else -math.inf,
                  lambda d: 0.0 if d == -math.inf else 10 ** ((d - 3) / 20), "dB"),
    "sec":       (lambda r: r, lambda d: d, "s"),
    "sq_pct":    (lambda r: 100 * r * r, lambda d: math.sqrt(max(d, 0) / 100), "%"),
    "pct":       (lambda r: 100 * r, lambda d: d / 100, "%"),
    "filter_hz": (lambda r: FA * math.exp(FB * r), lambda d: math.log(d / FA) / FB, "Hz"),
    "thresh_db": (lambda r: 60 * math.log10(1 - r) if r < 1 else -math.inf,
                  lambda d: 1.0 if d == -math.inf else 1 - 10 ** (d / 60), "dB"),
}

TABLE = {
    "osc":       {"volume": "gain_db"},
    "noise":     {"volume": "gain_db", "color": "pct"},
    "sub":       {"volume": "gain_db"},
    "voice":     {"volume": "main_db", "portamento_time": "sec"},
    "env":       {"attack": "sec", "hold": "sec", "decay": "sec", "release": "sec", "sustain": "sq_pct"},
    "filter":    {"freq": "filter_hz"},
    "wavetable": {"warp_amt": "pct"},
    "delay":     {"time_l": "sec", "time_r": "sec"},
    "distortion": {"freq": "filter_hz"},
    "filterfx":  {"freq": "filter_hz"},
    "comp":      {"thresh": "thresh_db", "makeup": "gain_db"},
}

LINE = re.compile(r"^(?:r\d_)?([a-z]+?)(\d*)((?:,\s*[a-z]+\d+)*)(\s+(?:on|off))?\s+\{")
PAIR = re.compile(r"(?<=[{\s])([a-z_0-9]+):([^\s{}]+)")
SIGFIGS = 5


def fmt_raw(v):
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = f"{v:.{SIGFIGS}g}"
    if "e" in s or "E" in s:
        s = f"{v:.{SIGFIGS + 4}f}".rstrip("0").rstrip(".")
    return s or "0"


def _short(x):
    if x == 0:
        return "0"
    digits = 2 if abs(x) >= 1 else max(2, 2 - math.floor(math.log10(abs(x))))
    s = f"{round(x, digits):.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _show(d, unit):
    if d == -math.inf:
        return f"-inf{unit}"
    if unit == "s" and abs(d) < 1:
        return f"{_short(d * 1000)}ms"
    return f"{_short(d)}{unit}"


def _parse(text, unit):
    if text.startswith("-inf"):
        return -math.inf
    if unit == "s" and text.endswith("ms"):
        return float(text[:-2]) / 1000
    if text.endswith(unit):
        return float(text[: -len(unit)])
    raise ValueError(f"no unit {unit!r} in {text!r}")


PASSED_RAW = []


def value_to_units(raw_text, sec, key):
    fwd, inv, unit = KINDS[TABLE[sec][key]]
    try:
        out = _show(fwd(float(raw_text)), unit)
        inv(_parse(out, unit))
        return out
    except (ValueError, OverflowError, ZeroDivisionError):
        PASSED_RAW.append((sec, key, raw_text))
        return raw_text


def value_to_raw(unit_text, sec, key):
    fwd, inv, unit = KINDS[TABLE[sec][key]]
    if not (unit_text.endswith(unit) or (unit == "s" and unit_text.endswith("ms"))):
        return unit_text
    return fmt_raw(inv(_parse(unit_text, unit)))


def _rewrite(line, conv):
    m = LINE.match(line)
    if not m or m.group(1) not in TABLE:
        return line
    sec, keys = m.group(1), TABLE[m.group(1)]
    start = line.index("{")
    end = line.index("}", start)
    body = PAIR.sub(lambda p: f"{p.group(1)}:{conv(p.group(2), sec, p.group(1))}"
                    if p.group(1) in keys else p.group(0), line[start:end + 1])
    return line[:start] + body + line[end + 1:]


def to_units(idc_text):
    return "\n".join(_rewrite(l, value_to_units) for l in idc_text.split("\n"))


def to_raw(idc_text):
    return "\n".join(_rewrite(l, value_to_raw) for l in idc_text.split("\n"))


def check(idc_text):
    unstable, changed, worst = [], 0, {}
    lines = idc_text.split("\n")
    for l in lines:
        u = _rewrite(l, value_to_units)
        changed += u != l
        if _rewrite(_rewrite(u, value_to_raw), value_to_units) != u:
            unstable.append(l)
        m = LINE.match(l)
        if not m or m.group(1) not in TABLE or u == l:
            continue
        sec = m.group(1)
        a = dict(PAIR.findall(l[l.index("{"):l.index("}") + 1]))
        b = dict(PAIR.findall(u[u.index("{"):u.index("}") + 1]))
        for k, kind in TABLE[sec].items():
            if k in a and k in b and a[k] != b[k]:
                r0, r1 = float(a[k]), float(value_to_raw(b[k], sec, k))
                e = abs(r1 - r0) / (abs(r0) if r0 else 1)
                if e > worst.get(kind, (0,))[0]:
                    worst[kind] = (round(e, 5), f"{sec}.{k}", a[k], b[k], fmt_raw(r1))
    return len(lines), changed, unstable, worst


if __name__ == "__main__":
    import sys
    src = open(sys.argv[2], encoding="utf-8").read()
    out = to_units(src) if sys.argv[1] == "units" else to_raw(src)
    if len(sys.argv) > 3:
        open(sys.argv[3], "w", encoding="utf-8").write(out)
    else:
        sys.stdout.write(out)
