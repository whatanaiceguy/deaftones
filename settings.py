"""settings.json beside this file, written by configure.py and read by everything else."""
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "settings.json"
DEFAULTS = {
    "models": str(ROOT / "models"),
    "cache": str(ROOT / "cache"),
    "out": str(ROOT / "out"),
    "gguf": "",
    "tokenizer": "",
    "llama_server": "",
    "hf_model": "",
    "vst": {"serum2": "", "vital": "", "diva": ""},
    "content": {"serum2": [], "vital": []},
}


def load():
    s = json.loads(json.dumps(DEFAULTS))
    if FILE.exists():
        got = json.loads(FILE.read_text(encoding="utf-8"))
        for k, v in got.items():
            s[k] = {**s[k], **v} if isinstance(s.get(k), dict) and isinstance(v, dict) else v
    return s


def save(s):
    FILE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def env(s=None):
    """The environment the converters and renderers read their paths from."""
    s = s or load()
    e = dict(os.environ, DEAFTONES_CACHE=s["cache"])
    for synth, path in s["vst"].items():
        if path:
            e[f"DEAFTONES_VST_{synth.upper()}"] = path
    return e


def apply(s=None):
    os.environ.update({k: v for k, v in env(s).items() if k.startswith("DEAFTONES_")})
