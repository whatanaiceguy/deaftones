"""Serum 2 playing the test pattern (render_pattern.py), at 120 BPM. The plugin path comes from
DEAFTONES_VST_SERUM2 (configure.py writes it)."""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import serum2_state  # noqa: E402
from render_pattern import pattern, LENGTH, SR, BLOCK  # noqa: E402

VST = os.environ.get("DEAFTONES_VST_SERUM2", r"C:\Program Files\Common Files\VST3\Serum2.vst3")
BPM = 120.0
_w = {}


def _fresh():
    import dawdreamer as daw
    eng = daw.RenderEngine(SR, BLOCK)
    syn = eng.make_plugin_processor("serum2", VST)
    eng.load_graph([(syn, [])])
    _w.update(eng=eng, syn=syn)
    if "tpl" not in _w:
        tmp = Path(tempfile.gettempdir()) / f"deaftones_serum2_{os.getpid()}.bin"
        syn.save_state(str(tmp))
        _w.update(tmp=tmp, tpl=serum2_state.Template(tmp.read_bytes()))


def render(path):
    _fresh()
    meta, data = serum2_state.read_preset(path)
    _w["tmp"].write_bytes(_w["tpl"].state(data))
    _w["syn"].load_state(str(_w["tmp"]))
    _w["eng"].set_bpm(BPM)
    _w["syn"].clear_midi()
    for note, start, length in pattern():
        _w["syn"].add_midi_note(note, 100, start, length)
    _w["eng"].render(LENGTH)
    return _w["eng"].get_audio(), data
