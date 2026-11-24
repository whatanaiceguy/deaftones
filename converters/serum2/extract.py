"""Finds Serum 2's own factory content in the user's Serum 2 folder, for restore_serum2.py.

Tables, noises and impulses are referenced by path and Serum loads them itself: every factory file
under Tables/, Samples/.../Noises/ and Impulses/ (not User/) goes into paths.json, so any of them
resolves and a name the model got wrong falls back to the nearest. What a preset embeds (a few
factory wavetables and samples) and the custom LFO shapes are kept when their content hash is in
factory.json. Written to <cache>/serum2/: paths.json, blobs.jsonl, blob_index.json, shapes.json.

    python extract.py "...\\Documents\\Xfer\\Serum 2 Presets" [FOLDER ...]
"""
import contextlib, hashlib, io, json, os, sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "render"))
import serum2_state as S2  # noqa: E402
import serum2_to_idc as T  # noqa: E402

CACHE = Path(os.environ.get("DEAFTONES_CACHE", HERE.parent.parent / "cache")) / "serum2"
MANIFEST = json.loads((HERE / "factory.json").read_text(encoding="utf-8"))


def key(lst):
    return hashlib.md5(json.dumps([round(float(x), 6) for x in lst]).encode()).hexdigest()


def numeric_lists(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from numeric_lists(v)
    elif isinstance(o, list):
        if len(o) >= 32 and all(isinstance(x, (int, float)) for x in o[:16]):
            yield o
        else:
            for v in o:
                yield from numeric_lists(v)


def factory_files(root, sub, exts):
    base = Path(root) / sub
    if not base.exists():
        return {}
    out = {}
    for p in base.rglob("*"):
        if p.suffix.lower() in exts and "User" not in p.relative_to(base).parts:
            rel = p.relative_to(base).as_posix()
            out[rel.replace(" ", "_")] = rel
    return out


def paths(folders):
    out = {"wtpath": {}, "noisepath": {}, "irpath": {}}
    for f in folders:
        out["wtpath"].update(factory_files(f, "Tables", {".wav"}))
        for noises in Path(f).joinpath("Samples").glob("*/Noises") if Path(f, "Samples").exists() else []:
            out["noisepath"].update(factory_files(noises.parent, "Noises", {".wav", ".flac"}))
        out["irpath"].update(factory_files(f, "Impulses", {".wav", ".flac"}))
    return out


def extract(folders, log=print):
    by_key, by_pts = {}, {}
    for i, h in MANIFEST["blobs"].items():
        by_key.setdefault(h, []).append(i)
    for i, h in MANIFEST["shapes"].items():
        by_pts.setdefault(h, []).append(i)
    files = sorted({p for f in folders for p in Path(f).rglob("*.SerumPreset")})
    log(f"Serum 2: reading {len(files)} presets")
    blobs, shapes, failed = {}, {}, 0
    for n, f in enumerate(files):
        try:
            head, body = S2.read_preset(str(f))
        except Exception:
            failed += 1
            continue
        for lst in numeric_lists(body):
            for i in by_key.get(key(lst), []):
                blobs[i] = lst
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                text = T.convert_preset({"metadata": head, "data": body}, n)
        except Exception:
            continue
        for m in T._SHAPE_RE.finditer(text):
            pts = m.group(2).strip()
            for i in by_pts.get(hashlib.md5(pts.encode("utf-8")).hexdigest(), []):
                shapes[i] = pts
    CACHE.mkdir(parents=True, exist_ok=True)
    if (CACHE / "blobs.jsonl").exists():
        for line in open(CACHE / "blobs.jsonl", encoding="utf-8"):
            r = json.loads(line)
            blobs.setdefault(r["id"], r["blob"])
    if (CACHE / "shapes.json").exists():
        for k, v in json.loads((CACHE / "shapes.json").read_text(encoding="utf-8")).items():
            shapes.setdefault(k, v)
    found = paths(folders)
    (CACHE / "paths.json").write_text(json.dumps(found, indent=1), encoding="utf-8")
    with open(CACHE / "blobs.jsonl", "w", encoding="utf-8") as out:
        for i, b in sorted(blobs.items()):
            out.write(json.dumps({"id": i, "blob": b}) + "\n")
    (CACHE / "blob_index.json").write_text(json.dumps(sorted(blobs)), encoding="utf-8")
    (CACHE / "shapes.json").write_text(json.dumps(dict(sorted(shapes.items())), indent=1), encoding="utf-8")
    stale = CACHE / "blob_lengths.json"
    if stale.exists():
        stale.unlink()
    log(f"Serum 2: {len(found['wtpath'])} factory tables, {len(found['noisepath'])} noises, "
        f"{len(found['irpath'])} impulses; {len(blobs)} of {len(MANIFEST['blobs'])} embedded blobs, "
        f"{len(shapes)} of {len(MANIFEST['shapes'])} LFO shapes ({failed} files unreadable)")
    return {"blobs": len(blobs), "shapes": len(shapes)}


if __name__ == "__main__":
    extract(sys.argv[1:])
