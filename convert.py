"""The model's answer to a playable preset and a render of the test pattern.

    python convert.py <answer.txt> [serum2|vital|diva]

convert(text, out_stem) writes <stem>.units.idc (the preset as the model wrote it), the synth's preset
file and <stem>.wav, and returns {synth, name, preset, wav, peak_db, notes}. Without the synth's plugin
in settings.json (configure.py), or without DawDreamer, the preset is written and not rendered.
"""
import importlib, importlib.util, io, json, re, subprocess, sys
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent
CONV = ROOT / "converters"
sys.path.insert(0, str(ROOT / "render"))
sys.path.insert(0, str(ROOT))
import settings  # noqa: E402

settings.apply()
PLUGIN = {"render_serum2": "serum2", "render_pattern": "vital", "render_diva": "diva"}

SYNTHS = {"serum2": "Serum 2", "vital": "Vital", "diva": "Diva"}
EXT = {"serum2": ".SerumPreset", "vital": ".vital", "diva": ".h2p"}


def answer(text):
    body = text.split("</think>", 1)[1] if "</think>" in text else text
    lines = [l for l in body.strip().splitlines() if not l.startswith(("@synth", "@group"))]
    synth = re.search(r"^@synth\s+(\w+)", text, re.M)
    name = re.search(r"^@preset\s+(.+)$", text, re.M)
    return ("\n".join(lines) + "\n", synth.group(1).lower() if synth else None,
            name.group(1).strip() if name else "generated")


def use(synth, module):
    d = CONV / synth
    for k, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None)
        if f and Path(f).parent.parent == CONV and Path(f).parent != d:
            del sys.modules[k]
    if str(d) in sys.path:
        sys.path.remove(str(d))
    sys.path.insert(0, str(d))
    m = importlib.import_module(module)
    assert Path(m.__file__).parent == d, (module, m.__file__)
    return m


def write_wav(audio, sr, dst):
    peak = float(np.abs(audio).max())
    sf.write(dst, (audio * min(1.0, 0.99 / max(peak, 1e-9))).T, sr, subtype="PCM_16")
    return round(20 * np.log10(peak + 1e-12), 1)


def render(module, preset, wav):
    if not settings.load()["vst"].get(PLUGIN[module]) or importlib.util.find_spec("dawdreamer") is None:
        return None
    r = subprocess.run([sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--render", module, str(preset), str(wav)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    peak = re.search(r"^PEAK (\S+)$", r.stdout, re.M)
    if r.returncode or not peak:
        raise RuntimeError(f"render failed ({r.returncode}): " + (r.stderr.strip().splitlines() or ["?"])[-1])
    return float(peak.group(1))


def serum2(idc, stem):
    units = use("serum2", "units")
    raw = stem.with_suffix(".idc")
    raw.write_text(units.to_raw(idc), encoding="utf-8")
    before = set(stem.parent.glob("*.SerumPreset"))
    r = subprocess.run([sys.executable, "-X", "utf8", "restore_serum2.py", str(raw), "--outdir", str(stem.parent), "-v"],
                       cwd=CONV / "serum2", capture_output=True, text=True, encoding="utf-8")
    notes = [l.strip() for l in r.stdout.splitlines() if "log:" in l or "drop" in l or "fallback" in l]
    preset = stem.with_suffix(".SerumPreset")
    new = [p for p in stem.parent.glob("*.SerumPreset") if p not in before and p != preset]
    if new:
        new[0].replace(preset)
        j = new[0].with_suffix(".json")
        if j.exists():
            j.replace(stem.with_suffix(".json"))
    if not preset.exists():
        raise RuntimeError("restore_serum2 wrote no preset: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    return preset, render("render_serum2", preset, stem.with_suffix(".wav")), notes


def vital(idc, stem):
    restore = use("vital", "restore")
    preset = stem.with_suffix(".vital")
    preset.write_text(json.dumps(restore.restore(idc)[0]), encoding="utf-8")
    return preset, render("render_pattern", preset, stem.with_suffix(".wav")), []


def diva(idc, stem):
    D = use("diva", "idc")
    preset = stem.with_suffix(".h2p")
    preset.write_bytes(D.from_idc(idc, strict=False))
    notes = [f"skipped: {s}" for s in D.SKIPPED]
    return preset, render("render_diva", preset, stem.with_suffix(".wav")), notes


def convert(text, stem, synth=None):
    idc, said, name = answer(text)
    synth = synth or said
    if synth not in SYNTHS:
        raise ValueError(f"unknown synth {synth!r}")
    stem = Path(stem)
    stem.with_suffix(".units.idc").write_text(idc, encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        preset, peak, notes = {"serum2": serum2, "vital": vital, "diva": diva}[synth](idc, stem)
    if peak is None:
        notes = notes + ["not rendered: no plugin or no DawDreamer (configure.py)"]
    wav = stem.with_suffix(".wav")
    return dict(synth=synth, name=name, preset=str(preset), wav=str(wav) if wav.exists() else None, peak_db=peak, notes=notes)


if __name__ == "__main__" and sys.argv[1] == "--render":
    R = importlib.import_module(sys.argv[2])
    with redirect_stdout(io.StringIO()):
        audio, _ = R.render(sys.argv[3])
    print("PEAK", write_wav(audio, R.SR, sys.argv[4]))
elif __name__ == "__main__":
    src = Path(sys.argv[1]).resolve()
    text = src.read_text(encoding="utf-8")
    name = answer(text)[2]
    print(json.dumps(convert(text, src.parent / re.sub(r"[^\w\- ]", "_", name)[:60], sys.argv[2] if len(sys.argv) > 2 else None), indent=1))
