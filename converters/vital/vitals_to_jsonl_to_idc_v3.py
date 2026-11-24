#!/usr/bin/env python3
"""
vital_pipeline.py
-----------------
Step 1: apply_refmap — reads reference_map_wordpair.json, applies it to .vital files,
        mints new word-pair IDs for unknown blobs, writes full_presets_merged.jsonl.
Step 2: (optional, prompted) vital_to_idc — converts full_presets_merged.jsonl to .idc

Usage:
    python vital_pipeline.py [folder] [--out FILE]
"""

import json, hashlib, argparse, sys, os, re
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill

# ============================================================================
# SHARED / apply_refmap
# ============================================================================

BASE_WORDS = [
    "silk", "rust", "bone", "crisp", "nitro",
    "haze", "veil", "edge", "peak", "tide", "pulse", "drift",
    "root", "fold", "press",
    "burn", "carve", "forge", "trace", "mark", "bind", "draw", "pour", "rise",
    "sharp", "hollow", "bright", "heavy", "light", "still", "swift", "rough",
    "narrow", "fierce", "obsidian",
    "tallow", "resin", "flint", "chalk", "loom", "husk", "burl", "tine", "weft", "clay",
    "jinx", "nexus", "keel", "pixel", "umbra", "zinc", "etched", "quartz", "joule", "fulcrum",
    "ibex", "moss", "patina", "stochastic", "sentinel", "dodecahedron", "abysmal"
]

FRAME_WORDS = [
    "quark", "zephyr", "nori", "velk", "gust", "holt", "ivar", "kelp", "loch", "orin",
    "pask", "taur", "ulv", "weld", "bryn", "casp"
]

TARGET_KEYS = {
    "samples":        "SAMPLES",
    "samples_stereo": "SAMPLES_STEREO",
    "audio_file":     "AUDIO_FILE",
    "wave_data":      "WAVE_DATA",
}

REFMAP_PATH = r"reference_map_wordpair.json"

def content_hash(content):
    return hashlib.md5(str(content).encode("utf-8")).hexdigest()

TRIPLET_PREFIXES = {"SAMPLES"}
#TRIPLET_PREFIXES = set()

def hash_to_pair(h, offset=0):
    total = len(BASE_WORDS)
    idx_a = int(h[0:4], 16) % total
    idx_b = (int(h[4:8], 16) + offset) % total
    if idx_b == idx_a:
        idx_b = (idx_b + 1) % total
    return [BASE_WORDS[idx_a], BASE_WORDS[idx_b]]

def hash_to_triplet(h, offset=0):
    total = len(BASE_WORDS)
    idx_a = int(h[0:4], 16) % total
    idx_b = (int(h[4:8], 16) + offset) % total
    idx_c = (int(h[8:12], 16) + offset) % total
    return [BASE_WORDS[idx_a], BASE_WORDS[idx_b], BASE_WORDS[idx_c]]

class Registry:
    def __init__(self, ref_map):
        self.ref_map = dict(ref_map)
        self.reverse = {}
        for word_id, blob in ref_map.items():
            for prefix in ("SAMPLES_STEREO", "AUDIO_FILE", "WAVE_DATA", "SAMPLES"):
                if word_id.startswith(prefix + "_"):
                    h = content_hash(blob)
                    cache_key = f"{prefix}::{h}"
                    if cache_key not in self.reverse:
                        self.reverse[cache_key] = word_id
                    break
        self.taken_ids = set(ref_map.keys())
        self.new_count = 0

    def get_or_mint(self, prefix, blob):
        h = content_hash(blob)
        cache_key = f"{prefix}::{h}"

        if cache_key in self.reverse:
            return self.reverse[cache_key]

        use_triplet = prefix in TRIPLET_PREFIXES
        max_attempts = len(BASE_WORDS) ** 3 if use_triplet else len(BASE_WORDS) ** 2

        offset = 0
        while offset < max_attempts:
            parts = hash_to_triplet(h, offset) if use_triplet else hash_to_pair(h, offset)
            word_id = f"{prefix}_{'_'.join(parts)}"
            if word_id not in self.taken_ids:
                break
            offset += 1
        else:
            raise RuntimeError(f"Exhausted all IDs for prefix={prefix} hash={h}")

        self.reverse[cache_key] = word_id
        self.taken_ids.add(word_id)
        self.ref_map[word_id]   = blob
        self.new_count         += 1
        return word_id

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(self.ref_map.items())), f, indent=4, ensure_ascii=False)

def replace_blobs(data, registry, path=None):
    if path is None:
        path = []
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in TARGET_KEYS:
                prefix = TARGET_KEYS[k]
                out[k] = registry.get_or_mint(prefix, v)
            else:
                out[k] = replace_blobs(v, registry, path + [k])
        return out
    elif isinstance(data, list):
        return [replace_blobs(item, registry, path + [i]) for i, item in enumerate(data)]
    return data

def count_ids(data):
    n = 0
    if isinstance(data, dict):
        for k, v in data.items():
            if k in TARGET_KEYS and isinstance(v, str) and "_" in v:
                n += 1
            else:
                n += count_ids(v)
    elif isinstance(data, list):
        for item in data:
            n += count_ids(item)
    return n

# ============================================================================
# vital_to_idc
# ============================================================================

UNCOLLAPSE_MODULATIONS = True

VOICE_PRIORITY = {0:"newest", 1:"oldest", 2:"highest", 3:"lowest", 4:"round_robin"}
VOICE_OVERRIDE = {0:"kill", 1:"steal"}
OSC_DESTINATION = {0:"filter1", 1:"filter2", 2:"filter1+2", 3:"effects", 4:"direct_out"}
SPECTRAL_MORPH_TYPE = {0:"none", 1:"vocode", 2:"formant_scale", 3:"harmonic_stretch", 4:"inharmonic_stretch", 5:"smear", 6:"random_amplitudes", 7:"low_pass", 8:"high_pass", 9:"phase_disperse", 10:"shepard_tone", 11:"spectral_time_skew", 12:"data_compress", 13:"spectral_filter", 14:"spectral_flanger", 15:"spectral_phaser"}
DISTORTION_TYPE = {0:"none", 1:"sync", 2:"formant", 3:"quantize", 4:"bend", 5:"squeeze", 6:"pulse", 7:"fm_osc2", 8:"fm_osc3", 9:"sample_fm", 10:"rm_osc2", 11:"rm_osc3", 12:"rm_sample"}
STACK_STYLE = {0:"unison", 1:"center_drop_12", 2:"center_drop_24", 3:"octave", 4:"octave_p7", 5:"2x_octave", 6:"power_chord", 7:"2x_power_chord", 8:"major_chord", 9:"minor_chord", 10:"harmonics", 11:"odd_harmonics", 12:"sub_harmonics"}

ENUM_KEYS = {
    "stack_style": STACK_STYLE,
    "destination": OSC_DESTINATION,
    "distortion_type": DISTORTION_TYPE,
    "spectral_morph_type": SPECTRAL_MORPH_TYPE,
}

DISTORTION_FX_TYPE = {
    0: "soft_clip", 1: "hard_clip", 2: "linear_fold",
    3: "sine_fold", 4: "bit_crush", 5: "down_sample"
}

DISTORTION_FX_FILTER_ORDER = {
    0: "none", 1: "pre", 2: "post"
}

COMPRESSOR_BANDS = {
    0: "multiband", 1: "low_band", 2: "high_band", 3: "single_band"
}

FILTER_MODEL = {
    0: "analog", 1: "dirty", 2: "ladder", 3: "digital",
    4: "diode", 5: "formant", 6: "comb", 7: "phaser", 8: "linear"
}

FILTER_STYLE = {
    "analog":   {0:"12db", 1:"24db", 2:"notch_blend", 3:"notch_spread", 4:"bpn"},
    "dirty":    {0:"12db", 1:"24db", 2:"notch_blend", 3:"notch_spread", 4:"bpn"},
    "ladder":   {0:"12db", 1:"24db", 2:"notch_blend", 3:"notch_spread", 4:"bpn"},
    "digital":  {0:"12db", 1:"24db", 2:"notch_blend", 3:"notch_spread", 4:"bpn"},
    "diode":    {0:"low_shelf", 1:"high_shelf"},
    "formant":  {0:"aoie", 1:"aiuo"},
    "comb":     {0:"low_high", 1:"low_high_flange+", 2:"low_high_flange-",
                 3:"spread_comb", 4:"spread_flange+", 5:"spread_flange-"},
    "phaser":   {0:"positive", 1:"negative"},
    "linear":   {0:"12db", 1:"24db", 2:"notch_blend", 3:"notch_spread", 4:"bpn"},
}

DELAY_STYLE = {
    0: "mono", 1: "stereo", 2: "ping_pong", 3: "mid_ping_pong"
}

OSC_KEYS = [
    "level", "pan", "tune", "transpose", "transpose_quantize",
    "wave_frame", "phase", "random_phase",
    "unison_voices", "unison_detune", "unison_blend",
    "detune_power", "detune_range",
    "stereo_spread", "stack_style", "destination",
    "distortion_type", "distortion_amount", "distortion_phase", "distortion_spread",
    "spectral_morph_type", "spectral_morph_amount", "spectral_morph_spread",
    "frame_spread", "spectral_unison",
]

FLOAT_PRECISION = {
    "level": 3,
    "unison_detune": 2,
    "band_resonance": 3,
    "band_gain": 3,
    "low_shelf_gain": 3,
    "delay": 3,
    "release": 3,
    "low_resonance": 3,
}

MOD_DECIMALS = 3

SEMITONE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

EFFECTS_BASE = [
    "chorus", "compressor", "delay", "distortion",
    "eq", "flanger", "filter", "phaser", "reverb",
]

def strip_quotes(path):
    return path.strip().strip('"').strip("'")

def detect_and_load(path):
    with open(path, encoding="utf-8") as f:
        first = f.readline().strip()
    if first.startswith("["):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    presets = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                presets.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  line {i+1} skip: {e}")
    return presets

_DEFAULTS = None


def load_defaults():
    global _DEFAULTS
    if _DEFAULTS is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "defaults.json"), encoding="utf-8") as f:
            _DEFAULTS = json.load(f).get("settings", {})
    return _DEFAULTS


MODULE_PREFIX = re.compile(r"^(osc_[123]|filter_[12]|filter_fx|sample|env_[1-6]|lfo_[1-8]|random_[1-4]|chorus|compressor|delay|distortion|eq|flanger|phaser|reverb)_")
GLOBAL_SKIP = re.compile(r"^(modulation_\d+_\w+|macro_control_\d+|mod_wheel|beats_per_minute|effect_chain_order)$")


def differs(k, v):
    ref = fill.value(k, load_defaults())
    return ref is None or isinstance(ref, (list, dict, str)) or abs(float(v) - float(ref)) > 5e-4


def extra(s, prefix, written):
    parts = []
    for k in sorted(s):
        if not k.startswith(prefix):
            continue
        sub = k[len(prefix):]
        v = s[k]
        if sub in written or sub == "on" or "view_" in sub or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if differs(k, v):
            parts.append(f"{sub}:{fmt(float(v))}")
    return (" " + " ".join(parts)) if parts else ""


def extra_global(s, written):
    parts = []
    for k in sorted(s):
        v = s[k]
        if k in written or MODULE_PREFIX.match(k) or GLOBAL_SKIP.match(k) or "view_" in k:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if differs(k, v):
            parts.append(f"{k}:{fmt(float(v))}")
    return (" " + " ".join(parts)) if parts else ""

def fmt_key(v, key=None):
    if isinstance(v, float) and key in FLOAT_PRECISION:
        prec = FLOAT_PRECISION[key]
        if v == int(v):
            return str(int(v))
        return f"{v:.{prec}f}"
    return fmt(v)

def fmt(v):
    if isinstance(v, float):
        r = round(v, 3)
        s = f"{r:.10f}".rstrip('0')
        if s.endswith('.'):
            s = s[:-1]
        return s
    return str(v)

def fmt_macro(v):
    v = round(v, 3)
    if v == int(v):
        return str(int(v))
    return f"{v:.3g}"

def fmt_transpose_quantize(value):
    value = int(value)
    if value == 0:
        return "none"
    parts = [SEMITONE_NAMES[i] for i in range(12) if value & (1 << i)]
    if value & (1 << 12):
        parts.append("global")
    return "+".join(parts)

def effect_chain_to_str(n):
    chain = decode_effect_order(n)
    return "effect_chain " + " > ".join(chain)

def normalize_bpm(raw_bpm):
    return round(raw_bpm * 60, 1)

def decode_effect_order(float_code):
    code = int(float_code)
    size = 9
    order = list(range(size))
    for i in range(size):
        remaining = size - i
        index = remaining - 1
        inversions = code % remaining
        code //= remaining
        placement = order[index - inversions]
        for j in range(index - inversions, index):
            order[j] = order[j + 1]
        order[index] = placement
    return [EFFECTS_BASE[o] for o in order]

def encode_effect_order(names):
    order = [EFFECTS_BASE.index(n) for n in names]
    size = len(order)
    code = 0
    for i in range(1, size):
        index = 0
        for j in range(i):
            index += order[i] < order[j]
        code *= i + 1
        code += index
    return code

def collapse_indexed(label, items, out):
    groups = {}
    for n, block in items:
        if block not in groups:
            groups[block] = []
        groups[block].append(n)
    for block, indices in groups.items():
        prefix = ", ".join(f"{label}{n}" for n in indices)
        out.append(f"{prefix} {block}")

def write_header(p, out):
    out.append("---")
    out.append(f'@preset {p.get("preset_name", "_")}')
    out.append(f'@author {p.get("author", "_") or "_"}')
    out.append(f'@category {p.get("preset_style", "_") or "_"}')
    comments = (p.get("comments", "_") or "_")
    comments = comments.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    out.append(f'@comments {comments}')
    s = p.get("settings", {})
    out.append(f'@bpm {normalize_bpm(s.get("beats_per_minute", 120))}')
    for i in range(1, 5):
        name = p.get(f"macro{i}", "_") or "_"
        out.append(f"@macro{i} {name}")

def write_delay(s, defaults, out):
    on = int(s.get("delay_on", 0.0))
    if not on:
        out.append("delay off _")
        return
    prefix = "delay_"
    style_int = int(s.get("delay_style", 0))
    style_str = DELAY_STYLE.get(style_int, "mono")
    keys_plain = ["frequency", "tempo", "sync", "aux_frequency", "aux_tempo",
                  "aux_sync", "feedback", "filter_cutoff", "filter_spread", "dry_wet"]
    params = f"style:{style_str} "
    params += " ".join(f"{k}:{fmt(s.get(prefix+k, defaults.get(prefix+k, 0.0)))}" for k in keys_plain)
    params += extra(s, prefix, set(keys_plain) | {"style"})
    out.append(f"delay on {{{params}}}")

def write_distortion(s, defaults, out):
    on = int(s.get("distortion_on", 0.0))
    if not on:
        out.append("distortion off _")
        return
    prefix = "distortion_"
    keys_plain = ["drive", "mix", "filter_blend", "filter_cutoff", "filter_resonance"]
    type_val = DISTORTION_FX_TYPE.get(int(s.get("distortion_type", 0)), "soft_clip")
    order_val = DISTORTION_FX_FILTER_ORDER.get(int(s.get("distortion_filter_order", 0)), "none")
    params = f"type:{type_val} filter_order:{order_val} "
    params += " ".join(f"{k}:{fmt(s.get(prefix+k, defaults.get(prefix+k, 0.0)))}" for k in keys_plain)
    params += extra(s, prefix, set(keys_plain) | {"type", "filter_order"})
    out.append(f"distortion on {{{params}}}")

def write_compressor(s, defaults, out):
    on = int(s.get("compressor_on", 0.0))
    if not on:
        out.append("compressor off _")
        return
    prefix = "compressor_"
    bands_int = int(s.get("compressor_enabled_bands", 0))
    bands_str = COMPRESSOR_BANDS.get(bands_int, "multiband")
    keys_plain = ["attack", "release", "mix",
                  "low_lower_threshold", "low_upper_threshold",
                  "low_lower_ratio", "low_upper_ratio", "low_gain",
                  "band_lower_threshold", "band_upper_threshold",
                  "band_lower_ratio", "band_upper_ratio", "band_gain",
                  "high_lower_threshold", "high_upper_threshold",
                  "high_lower_ratio", "high_upper_ratio", "high_gain"]
    params = f"enabled_bands:{bands_str} "
    params += " ".join(f"{k}:{fmt(s.get(prefix+k, defaults.get(prefix+k, 0.0)))}" for k in keys_plain)
    params += extra(s, prefix, set(keys_plain) | {"enabled_bands"})
    out.append(f"compressor on {{{params}}}")

def write_wavetable(wt, n, out):
    WAVETABLE_RANGE_MAP = {}
    _map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wavetable_range_map.json")
    if os.path.exists(_map_path):
        with open(_map_path, encoding="utf-8") as f:
            WAVETABLE_RANGE_MAP = json.load(f)

    name = re.sub(r"[{}\s]", "_", wt.get("name") or "_")
    groups = wt.get("groups", []) or []
    flags = (" normalize:0" if wt.get("full_normalize") is False else "") + (" remove_dc:0" if wt.get("remove_all_dc") is False else "")

    def styles(comp):
        return "".join(f" {k}:{int(comp[k])}" for k in ("interpolation", "interpolation_style") if k in comp)

    if not groups:
        out.append(f"wavetable{n} {{type:wave_source name:{name}}}")
        return

    components = groups[0].get("components") or []
    if len(groups) == 1 and not components:
        out.append(f"wavetable{n} {{type:empty name:{name}{flags}}}")
        return
    source_types = {c.get("type") for c in components}
    is_synthesized = (
        len(groups) > 1 or len(components) > 1 or
        bool(source_types - {"Wave Source", "Audio File Source"})
    )

    if not is_synthesized and source_types == {"Wave Source"}:
        comp = components[0]
        keyframes = comp.get("keyframes") or []
        if len(keyframes) == 1:
            wave_id = keyframes[0].get("wave_data", "_")
            out.append(f"wavetable{n} {{type:wave_source name:{name}{styles(comp)}{flags} data:{wave_id}}}")
        else:
            out.append(f"wavetable{n} {{type:wave_source name:{name}{styles(comp)}{flags}}}")
            for kf in keyframes:
                pos = kf.get("position", 0)
                wave_id = kf.get("wave_data", "_")
                out.append(f"{{position_{int(pos)} {wave_id}}}")
        return

    if not is_synthesized and source_types == {"Audio File Source"}:
        comp = components[0]
        fade_style = int(comp.get("fade_style", 1))
        phase_style = int(comp.get("phase_style", 0))
        seed = int(comp.get("random_seed", 0))
        window = fmt(float(comp.get("window_size", 2048)))
        audio_id = comp.get("audio_file", "_")
        rate = int(comp.get("audio_sample_rate", 44100))
        rate = f" sample_rate:{rate}" if rate != 44100 else ""
        rate += "".join(f" {k}:1" for k in ("normalize_gain", "normalize_mult") if comp.get(k))
        out.append(f"wavetable{n} {{type:audio_file name:{name}{styles(comp)}{flags}{rate} fade_style:{fade_style} phase_style:{phase_style} seed:{seed} window_size:{window} data:{audio_id}}}")
        for kf in (comp.get("keyframes") or []):
            pos = kf.get("position", 0)
            start = fmt(float(kf.get("start_position", 0)))
            real_range = WAVETABLE_RANGE_MAP.get(audio_id, 128)
            norm_pos = fmt(round(pos / real_range * 100, 2)) if real_range > 0 else pos
            more = ""
            if float(kf.get("window_size", 2048.0)) != 2048.0:
                more += f" window_size:{fmt(float(kf['window_size']))}"
            if float(kf.get("window_fade", 1.0)) != 1.0:
                more += f" window_fade:{fmt(float(kf['window_fade']))}"
            out.append(f"{{keyframe pos:{norm_pos} start:{start}{more}}}")
        return

    out.append(f"wavetable{n} {{type:synthesized name:{name}{flags}}}")
    timeline = {}

    for comp in (groups[0].get("components") or []):
        comp_type = comp.get("type", "")
        first_kf = True

        for kf in (comp.get("keyframes") or []):
            pos = kf.get("position", 0)
            key = f"position_{int(pos)}"
            if key not in timeline:
                timeline[key] = {}
            frame = timeline[key]

            if first_kf:
                if comp_type == "Audio File Source":
                    frame.setdefault("audio_file", comp.get("audio_file", "_"))
                    frame.setdefault("window_size", int(comp.get("window_size", 2048)))
                    frame.setdefault("fade_style", int(comp.get("fade_style", 0)))
                    frame.setdefault("phase_style", int(comp.get("phase_style", 0)))
                    frame.setdefault("seed", int(comp.get("random_seed", 0)))
                elif comp_type == "Wave Source":
                    frame.setdefault("interpolation", int(comp.get("interpolation", 1)))
                    frame.setdefault("style", comp.get("style", 0))
                    frame.setdefault("horizontal_asymmetric", int(comp.get("horizontal_asymmetric", False)))
                    frame.setdefault("vertical_asymmetric", int(comp.get("vertical_asymmetric", False)))
                elif comp_type == "Wave Warp":
                    frame.setdefault("warp_horizontal_asymmetric", int(comp.get("horizontal_asymmetric", False)))
                    frame.setdefault("warp_vertical_asymmetric", int(comp.get("vertical_asymmetric", False)))
                elif comp_type == "Wave Window":
                    frame.setdefault("window_shape", comp.get("window_shape", 0))
                elif comp_type == "Frequency Filter":
                    frame.setdefault("filter_style", comp.get("style", 0))
                frame["num_points"] = comp.get("num_points", 4)
                first_kf = False

            if comp_type == "Line Source":
                frame["num_points"] = comp.get("num_points", 4)

            for k, v in kf.items():
                if k == "position":
                    continue
                if k == "line":
                    points = v.get("points", [])
                    powers = v.get("powers", [])
                    pairs = []
                    for i in range(0, len(points) - 1, 2):
                        x = fmt(points[i])
                        y = fmt(points[i + 1])
                        power = fmt(powers[i // 2]) if i // 2 < len(powers) else "0"
                        pairs.append(f"{x},{y}|{power}")
                    frame["points_powers"] = " ".join(pairs)
                elif k == "wave_data":
                    frame["wave_data"] = v
                elif k == "audio_file":
                    frame.setdefault("audio_file", v)
                else:
                    frame[k] = v

    for key in sorted(timeline.keys(), key=lambda x: int(x.split("_")[1])):
        frame = timeline[key]
        parts = " ".join(f"{k}:{fmt(v) if not isinstance(v, str) else v}" for k, v in frame.items())
        out.append(f"{{{key} {parts}}}")


def write_osc(s, n, out):
    prefix = f"osc_{n}_"
    on = int(s.get(f"{prefix}on", 0.0))
    if not on:
        out.append(f"osc{n} off _")
        return
    OSC_ENUMS = {
        "stack_style": STACK_STYLE,
        "destination": OSC_DESTINATION,
        "distortion_type": fill.osc_distortion_names(n),
        "spectral_morph_type": SPECTRAL_MORPH_TYPE,
    }
    parts = []
    for k in OSC_KEYS:
        full_key = prefix + k
        if full_key not in s:
            continue
        raw = s[full_key]
        if k in OSC_ENUMS:
            val = OSC_ENUMS[k].get(int(raw), fmt(raw))
        elif k == "transpose_quantize":
            val = fmt_transpose_quantize(raw)
        else:
            val = fmt_key(raw, k)
        parts.append(f"{k}:{val}")
    params = " ".join(parts) + extra(s, prefix, set(OSC_KEYS))
    out.append(f"osc{n} on {{{params}}}")
    wt_index = n - 1
    wavetables = s.get("wavetables", [])
    if wt_index < len(wavetables):
        write_wavetable(wavetables[wt_index], n, out)


def write_sample(s, defaults, out):
    on = int(s.get("sample_on", 0.0))
    if not on:
        out.append("sample off _")
        return
    sample = s.get("sample", {})
    name = re.sub(r"[{}\s]", "_", sample.get("name") or "_")
    data = sample.get("samples", "_")
    keys = ["level", "pan", "tune", "transpose", "transpose_quantize",
            "keytrack", "loop", "bounce", "random_phase", "destination"]
    def fmt_sample_key(k):
        raw = s.get('sample_' + k, defaults.get('sample_' + k, 0.0))
        if k == 'transpose_quantize':
            return fmt_transpose_quantize(raw)
        if k == 'destination':
            return OSC_DESTINATION.get(int(raw), fmt(raw))
        return fmt(raw)
    params = " ".join(f"{k}:{fmt_sample_key(k)}" for k in keys) + extra(s, "sample_", set(keys))
    more = ""
    if sample.get("samples_stereo"):
        more += f" stereo:{sample['samples_stereo']}"
    if int(sample.get("length", 44100)) != 44100:
        more += f" length:{int(sample['length'])}"
    if int(sample.get("sample_rate", 44100)) != 44100:
        more += f" sample_rate:{int(sample['sample_rate'])}"
    out.append(f"sample on {{name:{name} data:{data}{more} {params}}}")


def write_filter(s, n, out):
    prefix = f"filter_{n}_"
    label = f"filter{n}"
    on = int(s.get(f"{prefix}on", 0.0))
    if not on:
        out.append(f"{label} off _")
        return
    model_int = int(s.get(f"{prefix}model", 0))
    style_int = int(s.get(f"{prefix}style", 0))
    model_str = FILTER_MODEL.get(model_int, "analog")
    style_str = FILTER_STYLE.get(model_str, {}).get(style_int, str(style_int))
    keys = ["cutoff", "resonance", "drive", "mix", "blend", "blend_transpose",
            "keytrack", "filter_input"]
    params = f"model:{model_str} style:{style_str} "
    params += " ".join(f"{k}:{fmt(s[prefix+k])}" for k in keys if prefix+k in s)
    params += extra(s, prefix, set(keys) | {"model", "style"})
    out.append(f"{label} on {{{params}}}")

def write_filterfx(s, out):
    prefix = "filter_fx_"
    on = int(s.get(f"{prefix}on", 0.0))
    if not on:
        out.append("filterfx off _")
        return
    model_int = int(s.get(f"{prefix}model", 0))
    style_int = int(s.get(f"{prefix}style", 0))
    model_str = FILTER_MODEL.get(model_int, "analog")
    style_str = FILTER_STYLE.get(model_str, {}).get(style_int, str(style_int))
    keys = ["cutoff", "resonance", "drive", "mix", "blend", "blend_transpose",
            "keytrack", "formant_resonance"]
    params = f"model:{model_str} style:{style_str} "
    params += " ".join(f"{k}:{fmt(s.get(prefix+k, 0.0))}" for k in keys if prefix+k in s)
    params += extra(s, prefix, set(keys) | {"model", "style"})
    out.append(f"filterfx on {{{params}}}")

def write_env(s, out):
    keys = ["attack", "attack_power", "decay", "decay_power",
            "sustain", "release", "release_power", "hold", "delay"]
    modulations = s.get("modulations", [])
    routed_sources = {m.get("source", "") for m in modulations}
    items = []
    off_items = []
    for n in range(1, 7):
        source_name = f"env_{n}"
        is_routed = source_name in routed_sources
        always_on = n < 4
        if always_on or is_routed:
            prefix = f"env_{n}_"
            has_keys = any(prefix + k in s for k in keys)
            if has_keys and not is_routed and always_on and n != 1:
                off_items.append(n)
            else:
                params = " ".join(f"{k}:{fmt(s[prefix+k])}" for k in keys if prefix+k in s) + extra(s, prefix, set(keys))
                items.append((n, f"{{{params}}}"))
    collapse_indexed("env", items, out)
    off_block_items = [(n, "unrouted _") for n in off_items]
    collapse_indexed("env", off_block_items, out)


def write_lfo(s, lfos, out):
    keys = ["frequency", "sync", "sync_type", "tempo",
            "smooth_time", "stereo", "keytrack_transpose"]
    modulations = s.get("modulations", [])
    routed_sources = {m.get("source", "") for m in modulations}
    items = []
    off_items = []
    for n in range(1, 9):
        source_name = f"lfo_{n}"
        is_routed = source_name in routed_sources
        always_on = n < 5
        if always_on or is_routed:
            prefix = f"lfo_{n}_"
            has_keys = any(prefix + k in s for k in keys)
            if has_keys and not is_routed and always_on:
                off_items.append(n)
            else:
                params = " ".join(f"{k}:{fmt(s[prefix+k])}" for k in keys if prefix+k in s) + extra(s, prefix, set(keys))
                if n - 1 < len(lfos) and isinstance(lfos[n - 1], dict) and lfos[n - 1].get("smooth"):
                    params += " smooth:1"
                shape_str = ""
                if n - 1 < len(lfos):
                    lfo_data = lfos[n - 1]
                    if isinstance(lfo_data, str):
                        shape_str = f" {{shape:{lfo_data}}}"
                    else:
                        name = re.sub(r"[{}\s]", "_", lfo_data.get("name") or "Triangle")
                        points_raw = lfo_data.get("points", [])
                        smooth = lfo_data.get("smooth", False)
                        powers = lfo_data.get("powers", [])
                        pairs = []
                        for i in range(0, len(points_raw) - 1, 2):
                            x = fmt(points_raw[i])
                            y = fmt(points_raw[i + 1])
                            power = fmt(powers[i // 2]) if i // 2 < len(powers) else "0"
                            pairs.append(f"{x},{y}|{power}")
                        points_str = " ".join(pairs)
                        shape_str = f" {{shape:{name} points_powers:{points_str}}}"
                block = f"{{{params}}}{shape_str}"
                items.append((n, block))
    collapse_indexed("lfo", items, out)
    off_block_items = [(n, "unrouted _") for n in off_items]
    collapse_indexed("lfo", off_block_items, out)


def write_random(s, out):
    keys = ["frequency", "sync", "sync_type", "tempo", "style",
            "stereo", "keytrack_transpose", "keytrack_tune"]
    modulations = s.get("modulations", [])
    routed_sources = {m.get("source", "") for m in modulations}
    items = []
    off_items = []
    for n in range(1, 5):
        source_name = f"random_{n}"
        is_routed = source_name in routed_sources
        prefix = f"random_{n}_"
        has_keys = any(prefix + k in s for k in keys)
        if has_keys and not is_routed:
            off_items.append(n)
        else:
            params = " ".join(f"{k}:{fmt(s[prefix+k])}" for k in keys if prefix+k in s) + extra(s, prefix, set(keys))
            items.append((n, f"{{{params}}}"))
    collapse_indexed("random", items, out)
    off_block_items = [(n, "unrouted _") for n in off_items]
    collapse_indexed("random", off_block_items, out)


def write_effect(s, name, prefix, keys, out, defaults):
    on_key = f"{prefix}on" if f"{prefix}on" in s else f"{name}_on"
    on = int(s.get(on_key, 0.0))
    if not on:
        out.append(f"{name} off _")
        return
    params = " ".join(
        f"{k}:{fmt(s.get(prefix+k, defaults.get(prefix+k, 0.0)))}"
        for k in keys
    ) + extra(s, prefix, set(keys))
    out.append(f"{name} on {{{params}}}")


def write_modulations(s, out):
    import re

    def normalize_name(x):
        x = re.sub(r'_(\d+)_', r'\1_', x)
        parts = x.split('_')
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0] + parts[1]
        return x

    modulations = s.get("modulations", [])
    out.append("modulations")

    if not modulations:
        out.append("_")
    elif UNCOLLAPSE_MODULATIONS:
        for i, mod in enumerate(modulations):
            n = i + 1
            source = normalize_name(mod.get("source", ""))
            destination = normalize_name(mod.get("destination", ""))
            if not source or not destination:
                continue
            amount = s.get(f"modulation_{n}_amount", 0.0)
            bipolar = s.get(f"modulation_{n}_bipolar", 0.0)
            stereo = s.get(f"modulation_{n}_stereo", 0.0)
            power = float(s.get(f"modulation_{n}_power", 0.0) or 0.0)
            tags = f"{destination}:{round(amount, MOD_DECIMALS)}"
            if bipolar == 1.0:
                tags += " bipolar:1"
            if stereo == 1.0:
                tags += " stereo:1"
            if abs(power) > 5e-4:
                tags += f" power:{fmt(power)}"
            if s.get(f"modulation_{n}_bypass", 0.0) == 1.0:
                tags += " bypass:1"
            out.append(f"[{n}] {source} -> {{{tags}}}")
    else:
        source_routes = {}
        for i, mod in enumerate(modulations):
            source = normalize_name(mod.get("source", ""))
            destination = normalize_name(mod.get("destination", ""))
            if not source or not destination:
                continue
            n = i + 1
            amount = s.get(f"modulation_{n}_amount", 0.0)
            bipolar = s.get(f"modulation_{n}_bipolar", 0.0)
            stereo = s.get(f"modulation_{n}_stereo", 0.0)
            tags = f"{destination}:{round(amount, 4)}"
            if bipolar == 1.0:
                tags += " bipolar:1"
            if stereo == 1.0:
                tags += " stereo:1"
            source_routes.setdefault(source, []).append(tags)
        for source, targets in source_routes.items():
            if len(targets) == 1:
                out.append(f"{source} -> {{{targets[0]}}}")
            else:
                inner = ", ".join(targets)
                out.append(f"{source} -> {{{inner}}}")

    out.append("")
    for n in range(1, 5):
        out.append(f"macro_control_{n} {fmt(s.get(f'macro_control_{n}', 0.0))}")
    out.append(f"mod_wheel {fmt(s.get('mod_wheel', 0.0))}")

def write_voice(s, out):
    keys = [
        ("polyphony", "polyphony"),
        ("pitch_bend_range", "pitch_bend"),
        ("legato", "legato"),
        ("portamento_time", "portamento_time"),
        ("portamento_slope", "portamento_slope"),
        ("portamento_force", "portamento_force"),
        ("volume", "volume"),
    ]
    VOICE_ENUMS = {
        "voice_priority": VOICE_PRIORITY,
        "voice_override": VOICE_OVERRIDE,
    }
    parts = []
    for key, label in keys:
        if key not in s:
            continue
        raw = s[key]
        if key in VOICE_ENUMS:
            val = VOICE_ENUMS[key].get(int(raw), fmt(raw))
        else:
            val = fmt(raw)
        parts.append(f"{label}:{val}")
    out.append(f"voice {{{' '.join(parts)}{extra_global(s, {k for k, _ in keys})}}}")


def convert_preset(p, defaults):
    out = []
    s = p.get("settings", {})
    lfos = s.get("lfos", [])

    write_header(p, out)
    out.append("")
    write_voice(s, out)
    out.append("")
    for n in range(1, 4):
        write_osc(s, n, out)
    write_sample(s, defaults, out)
    out.append("")
    for n in [1, 2]:
        write_filter(s, n, out)
    out.append("")
    write_env(s, out)
    out.append("")
    write_lfo(s, lfos, out)
    out.append("")
    write_random(s, out)
    out.append("")
    out.append(effect_chain_to_str(s.get("effect_chain_order", 0)))
    write_effect(s, "chorus", "chorus_", [
        "voices", "frequency", "tempo", "sync", "cutoff", "spread",
        "delay_1", "delay_2", "feedback", "mod_depth", "dry_wet"
    ], out, defaults)
    write_compressor(s, defaults, out)
    write_delay(s, defaults, out)
    write_distortion(s, defaults, out)
    write_effect(s, "eq", "eq_", [
        "low_cutoff", "low_resonance", "low_gain", "low_mode",
        "band_cutoff", "band_resonance", "band_gain", "band_mode",
        "high_cutoff", "high_resonance", "high_gain", "high_mode",
    ], out, defaults)
    write_filterfx(s, out)
    write_effect(s, "flanger", "flanger_", [
        "frequency", "tempo", "sync", "center", "feedback",
        "mod_depth", "phase_offset", "dry_wet"
    ], out, defaults)
    write_effect(s, "phaser", "phaser_", [
        "frequency", "tempo", "sync", "center", "feedback", "blend",
        "mod_depth", "phase_offset", "dry_wet"
    ], out, defaults)
    write_effect(s, "reverb", "reverb_", [
        "decay_time", "size", "delay", "dry_wet",
        "chorus_amount", "chorus_frequency",
        "pre_low_cutoff", "pre_high_cutoff",
        "low_shelf_cutoff", "low_shelf_gain",
        "high_shelf_cutoff", "high_shelf_gain",
    ], out, defaults)
    out.append("")
    write_modulations(s, out)
    return "\n".join(out)


# ============================================================================
# PIPELINE
# ============================================================================

def apply_main(folder, out_path):
    ref_path = Path(REFMAP_PATH)

    if not ref_path.exists():
        print(f"reference_map_wordpair.json not found — creating empty map.")
        ref_map = {}
        with open(ref_path, "w", encoding="utf-8") as f:
            json.dump({}, f)
    else:
        print(f"Loading reference map from {ref_path} ...")
        with open(ref_path, encoding="utf-8") as f:
            ref_map = json.load(f)
        print(f"  {len(ref_map)} entries loaded.")

    registry = Registry(ref_map)

    vital_files = sorted(folder.glob("*.vital"))
    
    if not vital_files:
        if out_path.exists():
            print(f"No .vital files found — but {out_path.name} exists, skipping to step 2.")
            return "jsonl_exists"
        print(f"No .vital files found in {folder} and no existing JSONL.")
        return False

    print(f"  {len(vital_files)} .vital file(s) found.\n")

    total_ids = 0
    skipped   = 0
    lines     = []

    for vf in vital_files:
        try:
            with open(vf, encoding="utf-8") as f:
                preset = json.load(f)
        except Exception as e:
            print(f"  [skip] {vf.name}: {e}")
            skipped += 1
            continue
        transformed = replace_blobs(preset, registry)
        ids = count_ids(transformed)
        total_ids += ids
        lines.append(json.dumps(transformed, ensure_ascii=False))
        print(f"  [ok]  {vf.name:<50} ids={ids}")

    # all blobs extracted — safe to write now
    with open(out_path, "w", encoding="utf-8") as out_f:
        for line in lines:
            out_f.write(line + "\n")

    registry.save(ref_path)

    print(f"\nStep 1 done.")
    print(f"  Files written   : {len(vital_files) - skipped}")
    print(f"  Files skipped   : {skipped}")
    print(f"  Total IDs placed: {total_ids}")
    print(f"  New IDs minted  : {registry.new_count}")
    print(f"  Ref map updated : {ref_path}")
    print(f"  Output          : {out_path}")
    return True



from collections import defaultdict, Counter
import re

def build_lfo_refmap(idc_lines):
    """
    Pass 1: scan all lfo shape blocks in the already-converted IDC lines,
    build frequency-sorted name->points_powers map.
    Returns: dict {final_name: points_powers_string}
    """
    # collect (shape, pp) occurrences
    shape_points_lookup = defaultdict(dict)   # shape -> {pp: prov_name}
    shape_points_index  = defaultdict(list)   # shape -> [pp in order]
    id_counter_prov     = Counter()

    def get_prov(shape, pp):
        lookup = shape_points_lookup[shape]
        if pp in lookup:
            return lookup[pp]
        idx = len(lookup)
        name = shape if idx == 0 else f"{shape}{idx + 1}"
        lookup[pp] = name
        shape_points_index[shape].append(pp)
        return name

    for line in idc_lines:
        line = line.strip()
        m = re.match(r'^((?:lfo[1-8]\s*,\s*)*lfo[1-8])\s+(.*)', line)
        if not m:
            continue
        indices = [int(x) for x in re.findall(r'lfo([1-8])', m.group(1))]
        remainder = m.group(2)
        all_braces = re.findall(r'\{[^}]*\}', remainder)
        if len(all_braces) < 2:
            continue
        shape_block = all_braces[1].strip().strip("{}")
        tokens = shape_block.split()
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
        if shape is None or pp is None:
            continue
        prov = get_prov(shape, pp)
        for _ in indices:
            id_counter_prov[prov] += 1

    # sort variants per base name by count desc, assign final names
    pp_to_final = {}   # (shape, pp) -> final_name
    for shape, pp_list in shape_points_index.items():
        variants = sorted(
            [(pp, id_counter_prov[shape_points_lookup[shape][pp]]) for pp in pp_list],
            key=lambda x: -x[1]
        )
        for rank, (pp, _) in enumerate(variants):
            final = shape if rank == 0 else f"{shape}{rank + 1}"
            pp_to_final[(shape, pp)] = final

    # build final map: name -> pp
    refmap = {}
    for (shape, pp), final in pp_to_final.items():
        refmap[final] = pp

    return refmap, pp_to_final, shape_points_lookup


def apply_lfo_refmap(idc_lines, pp_to_final, shape_points_lookup):
    out = []
    for line in idc_lines:
        stripped = line.strip()
        m = re.match(r'^((?:lfo[1-8]\s*,\s*)*lfo[1-8])\s+(.*)', stripped)
        if not m:
            out.append(line)
            continue
        remainder = m.group(2)
        all_braces = re.findall(r'\{[^}]*\}', remainder)
        if len(all_braces) < 2:
            out.append(line)
            continue

        shape_block = all_braces[1].strip().strip("{}")
        tokens = shape_block.split()
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

        if shape is None or pp is None:
            out.append(line)
            continue

        final = pp_to_final.get((shape, pp), shape)

        params = all_braces[0].strip().strip("{}")
        new_line = f"{m.group(1)} {{shape:{final} {params}}}"
        out.append(new_line)

    return out

def build_wt_refmap(idc_lines):
    shape_points_lookup = defaultdict(dict)
    shape_points_index  = defaultdict(list)
    id_counter_prov     = Counter()

    def get_prov(name, frames_str):
        lookup = shape_points_lookup[name]
        if frames_str in lookup:
            return lookup[frames_str]
        idx = len(lookup)
        prov = name if idx == 0 else f"{name}{idx + 1}"
        lookup[frames_str] = prov
        shape_points_index[name].append(frames_str)
        return prov

    i = 0
    while i < len(idc_lines):
        line = idc_lines[i].strip()
        m = re.match(r'^wavetable[1-3]\s+\{type:(synthesized|wave_source)\s+name:([^}\s]+)', line)
        if m:
            name = m.group(2)
            i += 1
            frames = []
            while i < len(idc_lines) and idc_lines[i].strip().startswith("{position_"):
                frames.append(idc_lines[i].strip())
                i += 1
            if frames:
                frames_str = "\n".join(frames)
                prov = get_prov(name, frames_str)
                id_counter_prov[prov] += 1
            continue
        i += 1

    frames_to_final = {}
    for name, frames_list in shape_points_index.items():
        variants = sorted(
            [(f, id_counter_prov[shape_points_lookup[name][f]]) for f in frames_list],
            key=lambda x: -x[1]
        )
        for rank, (frames_str, _) in enumerate(variants):
            final = name if rank == 0 else f"{name}{rank + 1}"
            frames_to_final[(name, frames_str)] = final

    refmap = {}
    for (name, frames_str), final in frames_to_final.items():
        refmap[final] = frames_str

    return refmap, frames_to_final, shape_points_lookup


def apply_wt_refmap(idc_lines, frames_to_final, shape_points_lookup):
    out = []
    i = 0
    while i < len(idc_lines):
        line = idc_lines[i].strip()
        m = re.match(r'^(wavetable[1-3])\s+(\{type:(synthesized|wave_source)\s+name:([^}\s]+)[^}]*\})', line)
        if m:
            wt_tag = m.group(1)
            meta_block = m.group(2)
            wt_type = m.group(3)
            name = m.group(4)
            i += 1
            frames = []
            while i < len(idc_lines) and idc_lines[i].strip().startswith("{position_"):
                frames.append(idc_lines[i].strip())
                i += 1
            if frames:
                frames_str = "\n".join(frames)
                final = frames_to_final.get((name, frames_str), name)
                new_meta = re.sub(r'name:[^}\s]+', f'name:{final}', meta_block)
                out.append(f"{wt_tag} {new_meta}")
            else:
                out.append(f"{wt_tag} {meta_block}")
            continue
        out.append(idc_lines[i])
        i += 1
    return out

def convert_main(jsonl_path, defaults):
    if not os.path.exists(jsonl_path):
        print(f"file not found: {jsonl_path}")
        return

    print(f"\nloading: {jsonl_path}")
    presets = detect_and_load(str(jsonl_path))
    print(f"loaded {len(presets)} presets")

    out_path = str(jsonl_path).replace(".jsonl", ".idc")
    refmap_path = str(jsonl_path).replace(".jsonl", "_lfo_refmap.json")

    # pass 1 — convert all presets to IDC lines in memory
    print("pass 1: converting presets...")
    all_lines = []
    written = 0
    for i, preset in enumerate(presets):
        try:
            dc = convert_preset(preset, defaults)
            all_lines.extend(dc.splitlines())
            all_lines.append("")
            written += 1
        except Exception as e:
            import traceback
            print(f"  preset {i+1} error: {e}")
            traceback.print_exc()

    print(f"pass 1 done: {written}/{len(presets)} presets converted")

    # pass 2 — build lfo refmap
    print("pass 2: building lfo refmap...")
    refmap, pp_to_final, shape_points_lookup = build_lfo_refmap(all_lines)
    with open(refmap_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(refmap.items())), f, indent=2, ensure_ascii=False)
    print(f"  {len(refmap)} shape IDs written to {refmap_path}")

    # pass 3 — apply lfo refmap
    print("pass 3: applying lfo refmap...")
    final_lines = apply_lfo_refmap(all_lines, pp_to_final, shape_points_lookup)

    # pass 4 — build wt refmap
    print("pass 4: building wavetable refmap...")
    wt_refmap_path = str(jsonl_path).replace(".jsonl", "_wt_refmap.json")
    wt_refmap, frames_to_final, wt_shape_lookup = build_wt_refmap(all_lines)
    with open(wt_refmap_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(wt_refmap.items())), f, indent=2, ensure_ascii=False)
    print(f"  {len(wt_refmap)} wavetable IDs written to {wt_refmap_path}")

    # pass 5 — apply wt refmap
    print("pass 5: applying wavetable refmap...")
    final_lines = apply_wt_refmap(final_lines, frames_to_final, wt_shape_lookup)

    # write final IDC once, after all remaps applied
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(final_lines))

    print(f"\nStep 2 done.")
    print(f"  Written   : {written}/{len(presets)} presets")
    print(f"  IDC       : {out_path}")
    print(f"  LFO refmap: {refmap_path}")
    print(f"  WT refmap : {wt_refmap_path}")


def main():
    ap = argparse.ArgumentParser(description="Vital pipeline: .vital → jsonl → idc")
    ap.add_argument("folder", nargs="?", default=".",
                    help="Folder with .vital files (default: .)")
    ap.add_argument("--out", metavar="FILE", default=None,
                    help="Output JSONL path (default: <folder>/full_presets_merged.jsonl)")
    ap.add_argument("--idc", action="store_true",
                    help="Convert to IDC, skipping step 1 if jsonl already exists")
    args = ap.parse_args()

    folder   = Path(args.folder).resolve()
    out_path = Path(args.out) if args.out else folder / "full_presets_merged.jsonl"

    if args.idc and out_path.exists():
        print("=" * 48)
        print("Step 2: jsonl → idc (skipping step 1)")
        print("=" * 48)
        defaults = load_defaults()
        convert_main(out_path, defaults)
        return

    print("=" * 48)
    print("Step 1: vital → jsonl")
    print("=" * 48)
    ok = apply_main(folder, out_path)
    
    if ok == "jsonl_exists":
        print()
        print("=" * 48)
        print("Step 2: jsonl → idc")
        print("=" * 48)
        defaults = load_defaults()
        convert_main(out_path, defaults)
        return
    
    if not ok:
        return

    if not args.idc:
        print()
        try:
            answer = input("convert to IDC? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nskipping IDC conversion")
            return
        if answer not in ("", "y", "yes"):
            print("skipping IDC conversion")
            return

    print()
    print("=" * 48)
    print("Step 2: jsonl → idc")
    print("=" * 48)
    defaults = load_defaults()
    convert_main(out_path, defaults)


if __name__ == "__main__":
    main()