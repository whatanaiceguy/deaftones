"""Adds Vital factory content from the user's own folders to the cache, for restore.py.

Every .vital, .vitaltable and .vitallfo under the given folders is named the way the training data
was. A blob, LFO shape or wavetable is kept when its name is in factory.json and its content has the
hash listed there. Written to <cache>/vital/: blob_names.json, blobs/<md5>.json, lfo_shapes.json,
wavetables.json.

    python extract.py FOLDER [FOLDER ...]
"""
import hashlib, json, os, re, struct, base64, sys
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import names  # noqa: E402
import idc_names as I  # noqa: E402

CACHE = Path(os.environ.get("DEAFTONES_CACHE", HERE.parent.parent / "cache")) / "vital"
MANIFEST = json.loads((HERE / "factory.json").read_text(encoding="utf-8"))
INIT_SAW = "WAVE_DATA_forge_drift_moss"
LFO_LINE = re.compile(r"^((?:lfo[1-8]\s*,\s*)*lfo[1-8])\s+(.*)")
md5 = lambda s: hashlib.md5(s.encode("utf-8")).hexdigest()
BY_HASH = {kind: {} for kind in ("lfo", "wt")}
for kind in BY_HASH:
    for n, h in MANIFEST[kind].items():
        BY_HASH[kind].setdefault(h, []).append(n)
_base = None


def base():
    global _base
    if _base is None:
        _base = json.loads((HERE / "defaults.json").read_text(encoding="utf-8"))
    return _base


def load(path):
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if p.suffix == ".vital":
        return data
    preset = json.loads(json.dumps(base()))
    if p.suffix == ".vitaltable":
        preset["settings"]["wavetables"][0] = data
    else:
        preset["settings"]["lfos"][0] = data
    return preset


def lfo_shapes(lines):
    out = []
    for line in lines:
        m = LFO_LINE.match(line.strip())
        if not m:
            continue
        braces = re.findall(r"\{[^}]*\}", m.group(2))
        if len(braces) < 2:
            continue
        tokens = braces[1].strip().strip("{}").split()
        shape = pp = None
        i = 0
        while i < len(tokens):
            if tokens[i].startswith("shape:"):
                shape = tokens[i].split(":", 1)[1]
            elif tokens[i].startswith("points_powers:"):
                parts = [tokens[i].split(":", 1)[1]]
                i += 1
                while i < len(tokens) and ":" not in tokens[i]:
                    parts.append(tokens[i])
                    i += 1
                pp = " ".join(parts)
                continue
            i += 1
        if shape is not None and pp is not None:
            out.append((shape, pp))
    return out


class Recorder:
    def __init__(self):
        self.blobs = {}

    def get_or_mint(self, prefix, blob):
        h = I.load_converter().content_hash(blob)
        name = names.name_for(prefix, h, I.group("blob", prefix))
        if MANIFEST["blob"].get(name) == h:
            self.blobs[name] = (h, blob)
        return name


def scan(path):
    try:
        conv = I.load_converter()
        rec = Recorder()
        preset = conv.replace_blobs(load(path), rec)
        lines = conv.convert_preset(preset, conv.load_defaults()).splitlines()
        lfo = {}
        for _, pp in lfo_shapes(lines):
            for n in BY_HASH["lfo"].get(md5(pp), []):
                lfo[n] = pp
        wt = {}
        wts = preset.get("settings", {}).get("wavetables") or []
        for _, _, k, _, _ in I.wavetable_frames(lines, wts):
            for n in BY_HASH["wt"].get(md5(json.dumps(wts[k - 1], sort_keys=True)), []):
                wt[n] = wts[k - 1]
        return rec.blobs, lfo, wt, None
    except Exception as e:
        return {}, {}, {}, f"{path}: {type(e).__name__}: {e}"


def saw():
    n = 2048
    return base64.b64encode(struct.pack(f"<{n}f", *[2 * i / n - 1 for i in range(n)])).decode()


def extract(folders, workers=8, log=print):
    files = sorted({str(p) for f in folders for ext in ("*.vital", "*.vitaltable", "*.vitallfo")
                    for p in Path(f).rglob(ext)})
    log(f"Vital: reading {len(files)} files")
    blobs, lfo, wt, failed = {}, {}, {}, 0
    with Pool(workers) as pool:
        for i, (b, l, w, err) in enumerate(pool.imap_unordered(scan, files, chunksize=8), 1):
            blobs.update(b)
            lfo.update(l)
            wt.update(w)
            failed += bool(err)
            if i % 500 == 0:
                log(f"  {i}/{len(files)}")
    (CACHE / "blobs").mkdir(parents=True, exist_ok=True)
    for name, (h, blob) in blobs.items():
        (CACHE / "blobs" / f"{h}.json").write_text(json.dumps(blob), encoding="utf-8")
    old = lambda f: json.loads((CACHE / f).read_text(encoding="utf-8")) if (CACHE / f).exists() else {}
    names_out = {n: h for n, h in old("blob_names.json").items() if (CACHE / "blobs" / f"{h}.json").exists()}
    names_out.update({n: h for n, (h, _) in blobs.items()})
    lfo = {**old("lfo_shapes.json"), **lfo}
    wt = {**old("wavetables.json"), **wt}
    if INIT_SAW not in names_out:
        h = md5("deaftones fallback saw")
        (CACHE / "blobs" / f"{h}.json").write_text(json.dumps(saw()), encoding="utf-8")
        names_out[INIT_SAW] = h
    (CACHE / "blob_names.json").write_text(json.dumps(names_out, indent=0, sort_keys=True), encoding="utf-8")
    (CACHE / "lfo_shapes.json").write_text(json.dumps(lfo, indent=0, sort_keys=True), encoding="utf-8")
    (CACHE / "wavetables.json").write_text(json.dumps(wt, indent=0, sort_keys=True), encoding="utf-8")
    found = {"blob": len([n for n in names_out if n in MANIFEST["blob"]]), "lfo": len(lfo), "wt": len(wt)}
    log(f"Vital: {found['blob']} of {len(MANIFEST['blob'])} blobs, {found['lfo']} of {len(MANIFEST['lfo'])} "
        f"LFO shapes, {found['wt']} of {len(MANIFEST['wt'])} wavetables ({failed} files unreadable)")
    return found


if __name__ == "__main__":
    extract(sys.argv[1:])
