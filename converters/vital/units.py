"""Raw Vital .idc <-> units .idc.

The raw .idc holds Vital's control values, which are not units: env attack 0.301 is 8.2 ms (quartic),
filter cutoff 41.438 is a note number (about 90 Hz), LFO frequency 2.083 is 4.24 Hz, volume 5473 is
-6 dB. The model writes units; this layer turns them back into raw values (and the other way):

    .vital -> vitals_to_jsonl_to_idc_v3 -> raw .idc -> to_units -> units .idc
    units .idc -> to_raw -> raw .idc -> restore_idc_fixer2 -> .vital

Precision: two decimals, at least three significant digits below 1, so the round trip is stable
(units -> raw -> units gives the same units) rather than exact on the raw text.

Curves are Vital's own: quartic times secs = raw^4; exponential secs/Hz = 2^raw (rates of modulators
are written in Hz, the delay's frequency as its time); note numbers are MIDI notes,
Hz = 440 * 2^((n - 69) / 12); volume dB = sqrt(raw) - 80; osc and sample level are squared gains, so
dB = 40 log10(raw); unison detune and resonances are shown as Vital shows them (raw^2, times 100 for
the resonances and reverb chorus amount).

Untouched: linear values (0-1 amounts, dB already in dB, semitone offsets), enums, wavetable and sample
data, LFO shapes, modulation route amounts (fractions of the target's raw range) and the macro / mod
wheel positions.
"""
import math
import re


def _note_hz(n): return 440.0 * 2 ** ((n - 69) / 12)
def _hz_note(f): return 69 + 12 * math.log2(f / 440.0)


# kind -> (raw to display, display to raw, unit suffix)
KINDS = {
    "quartic_s": (lambda r: r ** 4, lambda d: max(d, 0) ** 0.25, "s"),
    "exp_s":     (lambda r: 2 ** r, lambda d: math.log2(d), "s"),
    "exp_hz":    (lambda r: 2 ** r, lambda d: math.log2(d), "Hz"),
    "exp_ms":    (lambda r: 1000 * 2 ** r, lambda d: math.log2(d / 1000), "ms"),
    "period_s":  (lambda r: 2 ** -r, lambda d: -math.log2(d), "s"),
    "note_hz":   (_note_hz, _hz_note, "Hz"),
    "volume_db": (lambda r: math.sqrt(max(r, 0)) - 80, lambda d: (d + 80) ** 2, "dB"),
    "level_db":  (lambda r: 40 * math.log10(r) if r > 0 else -math.inf,
                  lambda d: 0.0 if d == -math.inf else 10 ** (d / 40), "dB"),
    "sq_pct":    (lambda r: r * r, lambda d: math.sqrt(max(d, 0)), "%"),
    "sq100_pct": (lambda r: 100 * r * r, lambda d: math.sqrt(max(d, 0) / 100), "%"),
}

NOTE = "note_hz"
TABLE = {
    "env":        {k: "quartic_s" for k in ("attack", "decay", "delay", "hold", "release")},
    "voice":      {"portamento_time": "exp_s", "volume": "volume_db"},
    "lfo":        {"frequency": "exp_hz", "smooth_time": "exp_s"},
    "random":     {"frequency": "exp_hz"},
    "osc":        {"level": "level_db", "unison_detune": "sq_pct"},
    "sample":     {"level": "level_db"},
    "filter":     {"cutoff": NOTE},
    "filterfx":   {"cutoff": NOTE},
    "chorus":     {"frequency": "exp_hz", "delay_1": "exp_ms", "delay_2": "exp_ms", "cutoff": NOTE},
    "flanger":    {"frequency": "exp_hz", "center": NOTE},
    "phaser":     {"frequency": "exp_hz", "center": NOTE},
    "delay":      {"frequency": "period_s", "aux_frequency": "period_s", "filter_cutoff": NOTE},
    "distortion": {"filter_cutoff": NOTE},
    "eq":         {"low_cutoff": NOTE, "band_cutoff": NOTE, "high_cutoff": NOTE,
                   "low_resonance": "sq100_pct", "band_resonance": "sq100_pct", "high_resonance": "sq100_pct"},
    "reverb":     {"decay_time": "exp_s", "chorus_frequency": "exp_hz", "chorus_amount": "sq100_pct",
                   "pre_low_cutoff": NOTE, "pre_high_cutoff": NOTE,
                   "low_shelf_cutoff": NOTE, "high_shelf_cutoff": NOTE},
}

# how vitals_to_jsonl_to_idc_v3 prints a raw value: fmt(), except write_osc's fmt_key
FIXED_DECIMALS = {("osc", "level"): 3, ("osc", "unison_detune"): 2}

LINE = re.compile(r"^([a-z]+?)(\d?)((?:,\s*[a-z]+\d)*)(\s+(?:on|off))?\s+\{")
PAIR = re.compile(r"(?<=[{\s])([a-z_0-9]+):([^\s{}]+)")


def fmt_raw(v, sec, key):
    d = FIXED_DECIMALS.get((sec, key))
    if d is not None:
        return str(int(v)) if v == int(v) else f"{v:.{d}f}"
    r = round(v, 3)
    s = f"{r:.10f}".rstrip("0")
    s = s[:-1] if s.endswith(".") else s
    return "0" if s in ("-0", "") else s


def _short(x):
    """Two decimals ("-6.02027dB" -> "-6.02dB", "89.544Hz" -> "89.54Hz"), trailing zeros dropped; below 1, at
    least three significant digits, so slow rates survive (0.105Hz, 0.00781Hz).
    Never an exponent."""
    if x == 0:
        return "0"
    digits = 2 if abs(x) >= 1 else max(2, 2 - math.floor(math.log10(abs(x))))
    r = round(x, digits)
    s = f"{r:.{digits}f}".rstrip("0").rstrip(".")
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


PASSED_RAW = []  # (sec, key, raw text) left raw: no exact units form


def value_to_units(raw_text, sec, key):
    """The value in units, cut to one decimal; a value with no units form (out of
    the curve's range, like the corpus's two negative osc levels) stays raw."""
    fwd, inv, unit = KINDS[TABLE[sec][key]]
    try:
        out = _show(fwd(float(raw_text)), unit)
        inv(_parse(out, unit))  # must invert
        return out
    except (ValueError, OverflowError, ZeroDivisionError):
        PASSED_RAW.append((sec, key, raw_text))
        return raw_text


def value_to_raw(unit_text, sec, key):
    fwd, inv, unit = KINDS[TABLE[sec][key]]
    if not (unit_text.endswith(unit) or (unit == "s" and unit_text.endswith("ms"))):
        return unit_text  # left raw by value_to_units
    return fmt_raw(inv(_parse(unit_text, unit)), sec, key)


def _rewrite(line, conv):
    m = LINE.match(line)
    if not m or m.group(1) not in TABLE:
        return line
    sec = m.group(1)
    keys = TABLE[sec]
    # only the first brace block: an LFO's second block is its shape
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
    """Units must be stable through raw: to_units(to_raw(u)) == u for every line.
    Returns (lines, changed, unstable lines, worst error per kind), the error as a
    fraction of the raw value's own range of movement (|raw back - raw| / |raw|,
    or absolute when raw is 0)."""
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
                    worst[kind] = (round(e, 5), f"{sec}.{k}", a[k], b[k], fmt_raw(r1, sec, k))
    return len(lines), changed, unstable, worst


if __name__ == "__main__":
    import sys
    src = open(sys.argv[2], encoding="utf-8").read()
    out = to_units(src) if sys.argv[1] == "units" else to_raw(src)
    if len(sys.argv) > 3:
        open(sys.argv[3], "w", encoding="utf-8").write(out)
    else:
        sys.stdout.write(out)
