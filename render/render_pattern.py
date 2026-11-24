"""Vital playing the test pattern the model's training audio was rendered with.

Vital's own plugin runs in DawDreamer; the .vital goes in as the plugin's state (juce_state.py), so
the pattern is real MIDI: overlapping notes, one chord, legato and glide wherever the preset has
them. The host tempo is the preset's own BPM. A fresh plugin per preset: a reused one keeps its
voices and its reverb and delay tails.

The pattern, velocity 100, 10.5 s:
  0.0 s   three single notes 0.9 s long, 1.2 s apart: C3 C4 C5
  3.6 s   legato, 4 notes 0.55 s long every 0.45 s: C4 D4 E4 G4
  5.7 s   staccato, 6 notes 80 ms long every 0.22 s: C4 E4 G4 C5 G4 E4
  7.2 s   C major 7th held 1.6 s, then its tail

The plugin path comes from DEAFTONES_VST_VITAL (configure.py writes it).
"""
import json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import juce_state  # noqa: E402

VST = os.environ.get("DEAFTONES_VST_VITAL", r"C:\Program Files\Common Files\VST3\Vital.vst3")
SR, BLOCK = 44100, 256
LENGTH = 10.5


def pattern(style=""):
    singles = [48, 60, 72]
    r = 60
    ev = [(n, 1.2 * i, 0.9) for i, n in enumerate(singles)]
    ev += [(r + n, 3.6 + 0.45 * i, 0.55) for i, n in enumerate([0, 2, 4, 7])]
    ev += [(r + n, 5.7 + 0.22 * i, 0.08) for i, n in enumerate([0, 4, 7, 12, 7, 4])]
    ev += [(r + n, 7.2, 1.6) for n in [0, 4, 7, 11]]
    return ev


_w = {}


def _fresh():
    import dawdreamer as daw
    eng = daw.RenderEngine(SR, BLOCK)
    syn = eng.make_plugin_processor("vital", VST)
    eng.load_graph([(syn, [])])
    _w.update(eng=eng, syn=syn)
    if "tpl" not in _w:
        tmp = Path(tempfile.gettempdir()) / f"deaftones_vital_{os.getpid()}.bin"
        syn.save_state(str(tmp))
        _w.update(tmp=tmp, tpl=juce_state.Template(tmp.read_bytes()))


def render(path):
    """(audio 2 x n float32, preset dict) for one .vital, in a fresh plugin."""
    _fresh()
    js = Path(path).read_bytes()
    preset = json.loads(js)
    _w["tmp"].write_bytes(_w["tpl"].state(js))
    _w["syn"].load_state(str(_w["tmp"]))
    _w["eng"].set_bpm(preset.get("settings", {}).get("beats_per_minute", 2.0) * 60)
    _w["syn"].clear_midi()
    for note, start, length in pattern():
        _w["syn"].add_midi_note(note, 100, start, length)
    _w["eng"].render(LENGTH)
    return _w["eng"].get_audio(), preset
