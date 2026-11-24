"""Units or raw .idc -> .vital preset dicts, through restore_idc_fixer2.

LFO shapes, wavetables and blobs are looked up by the names the model learned, in <cache>/vital/:
the content pack configure.py downloads, and anything extract.py adds from the user's own folders
(factory.json lists, per name, the hash its content must have). A name with no content there falls
back to the nearest known name of the same kind.
"""
import importlib.util
import json
import os
from pathlib import Path

import units

HERE = Path(__file__).parent
CACHE = Path(os.environ.get("DEAFTONES_CACHE", HERE.parent.parent / "cache")) / "vital"


def _load(name):
    p = CACHE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


class BlobStore(dict):
    """name -> blob, every name known up front, each blob read from disk on first use."""
    def __init__(self):
        super().__init__()
        self._md5 = _load("blob_names.json")

    def __contains__(self, name):
        return name in self._md5

    def __iter__(self):
        return iter(self._md5)

    def keys(self):
        return self._md5.keys()

    def __len__(self):
        return len(self._md5)

    def __getitem__(self, name):
        if not dict.__contains__(self, name):
            dict.__setitem__(self, name, json.loads((CACHE / "blobs" / f"{self._md5[name]}.json").read_text(encoding="utf-8")))
        return dict.__getitem__(self, name)


def _restorer():
    spec = importlib.util.spec_from_file_location("restore_idc_fixer2", HERE / "restore_idc_fixer2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.WAVETABLE_RANGE_MAP = {}
    return mod


def restore(idc_text: str) -> list:
    r = _restorer()
    blob_map = BlobStore()
    r._TYPE_INDEX = r.build_type_index(blob_map)
    presets = r.parse_idc(units.to_raw(idc_text), _load("lfo_shapes.json"), _load("wavetables.json"))
    for p in presets:
        r.decode_blobs(p.get("settings", {}), blob_map)
    return presets
