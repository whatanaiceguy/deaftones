"""One .vital -> .idc in memory, with every LFO shape, wavetable and blob named from its own content
by names.py and the seeds in vital_names.json, so a preset gets the names the model learned. Used by
extract.py to index the user's own Vital content.

usage: python idc_names.py PRESET.vital OUT.idc [--raw]
"""
import hashlib, importlib.util, json, re, sys
from pathlib import Path

VITAL = Path(__file__).parent
sys.path.insert(0, str(VITAL))
import names  # noqa: E402
import units  # noqa: E402

_conv = _groups = None


def load_converter():
    global _conv
    if _conv is None:
        spec = importlib.util.spec_from_file_location("vital_conv", VITAL / "vitals_to_jsonl_to_idc_v3.py")
        _conv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_conv)
    return _conv


def groups():
    global _groups
    if _groups is None:
        _groups = json.loads((VITAL / "vital_names.json").read_text(encoding="utf-8"))["groups"]
    return _groups


def group(kind, base):
    g = groups()[kind].get(base)
    return g if g is not None else {"k": 2, "seed": 0, "plain": None}


WT_META = re.compile(r"^wavetable([1-3])\s+(\{type:(?:synthesized|wave_source)\s+name:([^}\s]+)[^}]*\})")


def wt_key(frames, wt):
    return frames + "\n#" + hashlib.md5(json.dumps(wt, sort_keys=True).encode()).hexdigest()


def wavetable_frames(lines, wts):
    """(line index, end, osc, base name, key) for every synthesized or wave-source wavetable without inline data."""
    found, i = [], 0
    while i < len(lines):
        m = WT_META.match(lines[i].strip())
        j = i + 1
        if m:
            frames = []
            while j < len(lines) and lines[j].strip().startswith("{position_"):
                frames.append(lines[j].strip())
                j += 1
            if frames or "data:" not in m.group(2):
                n = int(m.group(1))
                found.append((i, j, n, m.group(3), wt_key("\n".join(frames), wts[n - 1] if n - 1 < len(wts) else None)))
        i = j
    return found


def name_wavetables(lines, wts):
    out, last = [], 0
    for i, j, n, base, key in wavetable_frames(lines, wts):
        out += lines[last:i]
        meta = WT_META.match(lines[i].strip()).group(2)
        out.append(f"wavetable{n} " + re.sub(r"name:[^}\s]+", "name:" + names.name_for(base, key, group("wt", base)), meta, count=1))
        last = j
    return out + lines[last:]


class NameRegistry:
    """Stands in for the converter's Registry: a blob's name comes from its md5."""
    def __init__(self):
        self.used = {}

    def get_or_mint(self, prefix, blob):
        h = load_converter().content_hash(blob)
        name = names.name_for(prefix, h, group("blob", prefix))
        self.used[name] = h
        return name


def vital_to_idc(path: Path, in_units: bool = True, canonical: bool = True):
    """Returns (idc text, {blob name: md5} for the blobs this preset uses)."""
    return preset_to_idc(json.loads(path.read_text(encoding="utf-8")), in_units, canonical)


def preset_to_idc(preset: dict, in_units: bool = True, canonical: bool = True):
    """canonical: converted, restored and converted again, so what does not sound (routes
    into modules that are off, a destination into a filter that is off, an effect at
    zero mix) has one spelling and converting a restored preset gives the same text."""
    text, used = _convert(preset)
    if canonical:
        import contextlib, io, restore
        with contextlib.redirect_stdout(io.StringIO()):
            [back] = restore.restore(text)
        text, used = _convert(back)
    return (units.to_units(text) if in_units else text), used


def _convert(preset: dict):
    conv = load_converter()
    reg = NameRegistry()
    preset = conv.replace_blobs(json.loads(json.dumps(preset)), reg)
    lines = conv.convert_preset(preset, conv.load_defaults()).splitlines()
    _, _, lfo_lookup = conv.build_lfo_refmap(lines)
    lfo_names = {(shape, pp): names.name_for(shape, pp, group("lfo", shape))
                 for shape, pps in lfo_lookup.items() for pp in pps}
    out = conv.apply_lfo_refmap(lines, lfo_names, lfo_lookup)
    out = name_wavetables(out, preset.get("settings", {}).get("wavetables") or [])
    return "\n".join(out).strip() + "\n", reg.used


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.parent.mkdir(parents=True, exist_ok=True)
    text, _ = vital_to_idc(src, in_units="--raw" not in sys.argv)
    dst.write_text(text, encoding="utf-8")
    print(dst)
