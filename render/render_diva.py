"""Diva playing the test pattern (render_pattern.py), at 120 BPM and Divine accuracy. The plugin path
comes from DEAFTONES_VST_DIVA (configure.py writes it)."""
import os, struct, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import juce_state  # noqa: E402
from render_pattern import pattern, LENGTH, SR, BLOCK  # noqa: E402

VST = os.environ.get("DEAFTONES_VST_DIVA", r"C:\Program Files\Common Files\VST3\Diva(x64).vst3")
BPM = 120.0
WARM = 0.5
ACCURACY = 1.0
_w = {}


def state(name, text):
    raw = f"#pgm={name}\n".encode("utf-8") + text
    b = juce_state.b64_encode(struct.pack("<I", len(raw)) + raw)
    xml = ('<?xml version="1.0" encoding="UTF-8"?> <VST3PluginState><IComponent>' + b
           + "</IComponent><IEditController>" + b + "</IEditController></VST3PluginState>").encode() + b"\x00"
    return b"VC2!" + struct.pack("<I", len(xml) - 1) + xml


def _fresh():
    import dawdreamer as daw
    eng = daw.RenderEngine(SR, BLOCK)
    syn = eng.make_plugin_processor("diva", VST)
    eng.load_graph([(syn, [])])
    if "acc" not in _w:
        _w["acc"] = next(i for i in range(syn.get_plugin_parameter_size()) if syn.get_parameter_name(i) == "Accuracy")
        _w["tmp"] = Path(tempfile.gettempdir()) / f"deaftones_diva_{os.getpid()}.bin"
    _w.update(eng=eng, syn=syn)


def render(path):
    _fresh()
    text = Path(path).read_bytes()
    _w["tmp"].write_bytes(state(Path(path).name, text))
    syn, eng = _w["syn"], _w["eng"]
    syn.load_state(str(_w["tmp"]))
    syn.set_parameter(_w["acc"], ACCURACY)
    eng.set_bpm(BPM)
    syn.clear_midi()
    eng.render(WARM)
    syn.clear_midi()
    for note, start, length in pattern():
        syn.add_midi_note(note, 100, start, length)
    eng.render(LENGTH)
    return eng.get_audio(), text
