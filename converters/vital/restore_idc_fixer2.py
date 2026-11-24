import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill
import names

# ─── toggles ──────────────────────────────────────────────────────────────────
RESTORE_VITAL = True          # True = individual .vital files, False = single .jsonl
OUTPUT_NEXT_TO_INPUT = True  # True = output beside input file, False = beside script
USE_SUBFOLDER      = True # True = wrap output in _vital/_restored subfolder, False = dump flat beside script
FORCE_EFFECTS_ON = False
DEFAULT_FILE       = "test.txt" # filename to use when Enter is pressed with no input (relative to script dir)
# ──────────────────────────────────────────────────────────────────────────────

SYNTH_VERSION = "1.5.5"

WAVETABLE_RANGE_MAP = {}

DEFAULT_LFO = {
    "name": "Triangle",
    "num_points": 3,
    "points": [0.0, 1.0, 0.5, 0.0, 1.0, 1.0],
    "powers": [0.0, 0.0, 0.0],
    "smooth": False,
}

DEFAULT_WARP = {
    "name": "Triangle",
    "num_points": 3,
    "points": [0.0, 1.0, 0.5, 0.0, 1.0, 1.0],
    "powers": [0.0, 0.0, 0.0],
    "smooth": False,
}

FADE_STYLE = {
    0: "loop",
    1: "ping_pong",
    2: "single_cycle",
    3: "freeze",
}
FADE_STYLE_R = {v: k for k, v in FADE_STYLE.items()}

PHASE_STYLE = {
    0: "clear",
    1: "copy",
    2: "random",
}
PHASE_STYLE_R = {v: k for k, v in PHASE_STYLE.items()}

DEFAULT_RANDOM_VALUE = {"seed": 4}

VOICE_PRIORITY_R = {"newest":0, "oldest":1, "highest":2, "lowest":3, "round_robin":4}
VOICE_OVERRIDE_R = {"kill":0, "steal":1}
OSC_DESTINATION_R = {"filter1":0, "filter2":1, "filter1+2":2, "effects":3, "direct_out":4}
SPECTRAL_MORPH_TYPE_R = {"none":0, "vocode":1, "formant_scale":2, "harmonic_stretch":3, "inharmonic_stretch":4, "smear":5, "random_amplitudes":6, "low_pass":7, "high_pass":8, "phase_disperse":9, "shepard_tone":10, "spectral_time_skew":11, "data_compress":12, "spectral_filter":13, "spectral_flanger":14, "spectral_phaser":15}
DISTORTION_TYPE_R = {"none":0, "sync":1, "formant":2, "quantize":3, "bend":4, "squeeze":5, "pulse":6, "fm_osc2":7, "fm_osc3":8, "sample_fm":9, "rm_osc2":10, "rm_osc3":11, "rm_sample":12}
STACK_STYLE_R = {"unison":0, "center_drop_12":1, "center_drop_24":2, "octave":3, "octave_p7":4, "2x_octave":5, "power_chord":6, "2x_power_chord":7, "major_chord":8, "minor_chord":9, "harmonics":10, "odd_harmonics":11, "sub_harmonics":12}

EFFECTS_BASE = [
    "chorus", "compressor", "delay", "distortion",
    "eq", "flanger", "filterfx", "phaser", "reverb",
]

DISTORTION_FX_TYPE = {
    0: "soft_clip",
    1: "hard_clip",
    2: "linear_fold",
    3: "sine_fold",
    4: "bit_crush",
    5: "down_sample"
}

DISTORTION_FX_FILTER_ORDER = {
    0: "none",
    1: "pre",
    2: "post"
}

DISTORTION_FX_TYPE_R = {v: k for k, v in DISTORTION_FX_TYPE.items()}
DISTORTION_FX_FILTER_ORDER_R = {v: k for k, v in DISTORTION_FX_FILTER_ORDER.items()}

COMPRESSOR_BANDS = {
    0: "multiband",
    1: "low_band",
    2: "high_band",
    3: "single_band"
}

COMPRESSOR_BANDS_R = {v: k for k, v in COMPRESSOR_BANDS.items()}

FILTER_MODEL = {
    0: "analog", 1: "dirty", 2: "ladder", 3: "digital",
    4: "diode", 5: "formant", 6: "comb", 7: "phaser", 8: "linear"
}

FILTER_MODEL_R = {v: k for k, v in FILTER_MODEL.items()}

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

FILTER_STYLE_R = {
    model: {v: k for k, v in styles.items()}
    for model, styles in FILTER_STYLE.items()
}

DELAY_STYLE = {
    0: "mono",
    1: "stereo",
    2: "ping_pong",
    3: "mid_ping_pong"
}
DELAY_STYLE_R = {v: k for k, v in DELAY_STYLE.items()}

ON_KEYS = {
    "filter_1_on", "filter_2_on", "filter_fx_on",
    "eq_on", "flanger_on", "osc_1_on", "osc_2_on", "osc_3_on",
    "phaser_on", "reverb_on", "sample_on", "chorus_on",
    "compressor_on", "delay_on", "distortion_on",
}

DEST_PREFIX_MAP = {k[:-2]: k for k in ON_KEYS}

def reconstruct_synthesized_wavetable(frames, components_order=None):
    if not frames:
        return []

    FIELD_TO_COMP = {
        "wave_data": "Wave Source",
        "interpolation": "Wave Source",
        "audio_file": "Audio File Source",
        "window_size": "Audio File Source",
        "fade_style": "Audio File Source",
        "phase_style": "Audio File Source",
        "seed": "Audio File Source",
        "normalize_gain": "Audio File Source",
        "normalize_mult": "Audio File Source",
        "start_position": "Audio File Source",
        "window_fade": "Audio File Source",
        "down_run_rise": "Slew Limiter",
        "up_run_rise": "Slew Limiter",
        "fold_boost": "Wave Folder",
        "normalize": "Frequency Filter",
        "filter_style": "Frequency Filter",
        "cutoff": "Frequency Filter",
        "shape": "Frequency Filter",
        "warp_horizontal_asymmetric": "Wave Warp",
        "warp_vertical_asymmetric": "Wave Warp",
        "horizontal_asymmetric": "Wave Source",
        "vertical_asymmetric": "Wave Source",
        "horizontal_power": "Wave Warp",
        "vertical_power": "Wave Warp",
        "window_shape": "Wave Window",
        "left_position": "Wave Window",
        "right_position": "Wave Window",
        "num_points": "Line Source",
        "points_powers": "Line Source",
        "pull_power": "Line Source",
        "line_name": "Line Source",
    }

    SCHEMA = {
        "Wave Source": {
            "static": {
                "interpolation": ("interpolation", 1),
                "style": ("style", 0),
                "horizontal_asymmetric": ("horizontal_asymmetric", False),
                "vertical_asymmetric": ("vertical_asymmetric", False),
            },
            "keyframe": {"wave_data"},
        },
        "Audio File Source": {
            "static": {
                "audio_file": ("audio_file", "_"),
                "window_size": ("window_size", 2048.0),
                "fade_style": ("fade_style", 0),
                "phase_style": ("phase_style", 0),
                "seed": ("random_seed", 0),
                "normalize_gain": ("normalize_gain", False),
                "normalize_mult": ("normalize_mult", False),
            },
            "keyframe": {"start_position", "window_fade"},
        },
        "Slew Limiter": {
            "static": {},
            "keyframe": {"down_run_rise", "up_run_rise"},
        },
        "Wave Folder": {
            "static": {},
            "keyframe": {"fold_boost"},
        },
        "Frequency Filter": {
            "static": {
                "normalize": ("normalize", True),
                "filter_style": ("style", 0),
            },
            "keyframe": {"cutoff", "shape"},
        },
        "Wave Warp": {
            "static": {
                "warp_horizontal_asymmetric": ("horizontal_asymmetric", False),
                "warp_vertical_asymmetric": ("vertical_asymmetric", False),
            },
            "keyframe": {"horizontal_power", "vertical_power"},
        },
        "Wave Window": {
            "static": {"window_shape": ("window_shape", 0)},
            "keyframe": {"left_position", "right_position"},
        },
        "Line Source": {
            "static": {"num_points": ("num_points", 4)},
            "keyframe": {"points_powers", "pull_power"},
        },
    }

    has_audio_file = any("audio_file" in fields for _, fields in frames)

    types_needed = set()
    for pos, fields in frames:
        for k in fields:
            ct = FIELD_TO_COMP.get(k)
            if ct is None:
                continue
            if ct == "Audio File Source" and not has_audio_file:
                continue
            types_needed.add(ct)

    if components_order:
        type_order = [t for t in components_order if t in types_needed]
    else:
        seen = []
        seen_set = set()
        for pos, fields in frames:
            for k in fields:
                ct = FIELD_TO_COMP.get(k)
                if ct and ct not in seen_set:
                    if ct == "Audio File Source" and not has_audio_file:
                        continue
                    seen_set.add(ct)
                    seen.append(ct)
        type_order = seen

    components = []

    for comp_type in type_order:
        schema = SCHEMA[comp_type]

        # only Audio File Source gets interpolation_style and audio_sample_rate
        if comp_type == "Audio File Source":
            comp = {
                "type": comp_type,
                "interpolation_style": 1,
                "audio_sample_rate": 44100,
            }
        else:
            comp = {"type": comp_type}

        static_vals = {}
        kf_map = {}

        for pos, fields in frames:
            for k, v in fields.items():
                if FIELD_TO_COMP.get(k) != comp_type:
                    continue
                if k in schema["static"]:
                    if k not in static_vals:
                        static_vals[k] = v
                elif k in schema["keyframe"]:
                    kf_map.setdefault(pos, {})[k] = v

        # apply static fields
        for k, v in static_vals.items():
            target, default = schema["static"][k]
            if isinstance(default, bool):
                comp[target] = (str(v) == "1" or str(v).lower() == "true")
            elif isinstance(default, int):
                comp[target] = int(try_float(v))
            else:
                comp[target] = try_float(v)

        # hardcoded overrides
        if comp_type == "Frequency Filter":
            comp["normalize"] = True
        if comp_type == "Audio File Source":
            comp["normalize_gain"] = False
            comp["normalize_mult"] = False

        # defaults for anything still missing
        for k, (target, default) in schema["static"].items():
            if target not in comp:
                comp[target] = default

        keyframes = []
        for pos in sorted(kf_map.keys()):
            kf = {"position": int(pos)}  # always int
            frame_fields = kf_map[pos]

            if comp_type == "Line Source":
                if "points_powers" in frame_fields:
                    points, powers = [], []
                    for token in str(frame_fields["points_powers"]).split():
                        xy, power = (token.split("|", 1) if "|" in token else (token, "0"))
                        if "," in xy:
                            x, y = xy.split(",", 1)
                            points.extend([try_float(x), try_float(y)])
                            powers.append(try_float(power))
                    num_points = len(points) // 2
                    kf["line"] = {
                        "name": str(frame_fields.get("line_name", "Linear")).replace("_", " "),
                        "num_points": num_points,
                        "points": points,
                        "powers": powers,
                        "smooth": False,
                    }
                    comp["num_points"] = num_points
                if "pull_power" in frame_fields:
                    kf["pull_power"] = try_float(frame_fields["pull_power"])
            else:
                for k, v in frame_fields.items():
                    kf[k] = try_float(v)

            # Audio File Source keyframes always get window_size
            if comp_type == "Audio File Source":
                kf.setdefault("window_size", 2048.0)

            keyframes.append(kf)

        # Wave Window fallback — ensure left/right present on all keyframes
        if comp_type == "Wave Window":
            import random
            for kf in keyframes:
                if "left_position" not in kf:
                    kf["left_position"] = round(random.uniform(0.0, 0.4), 3)
                if "right_position" not in kf:
                    kf["right_position"] = round(random.uniform(0.6, 1.0), 3)
            if len(keyframes) == 1:
                keyframes.append({
                    "position": 256,
                    "left_position": round(random.uniform(0.0, 0.4), 3),
                    "right_position": round(random.uniform(0.6, 1.0), 3),
                })

        if not keyframes:
            keyframes.append({"position": 0})

        comp["keyframes"] = keyframes
        components.append(comp)

    return components

# ---------------------------------------------------------------------------
# LOGGER
# ---------------------------------------------------------------------------

import sys

DEBUG_LOG = False  # override with --debug arg

class PresetLogger:
    def __init__(self):
        self.mode = "normal"
        self._runs = []        # list of (preset_name, [(label, message)])
        self._current = None   # (preset_name, [(label, message)])
        self._counts = {"fallback": 0, "fix": 0, "warn": 0}

    def reset(self):
        self._runs = []
        self._current = None
        self._counts = {"fallback": 0, "fix": 0, "warn": 0}

    def begin_preset(self, name):
        self._current = (name, [])

    def rename_preset(self, name):
        if self._current is not None:
            self._current = (name, self._current[1])

    def log(self, label, message):
        if self._current is None:
            return
        self._current[1].append((label, message))
        self._counts[label] += 1
        if len(self._current[1]) == 1:
            print(f"  [{self._current[0]}]")
        print(f"    {label}: {message}")

    def end_preset(self):
        if self._current and self._current[1]:
            self._runs.append(self._current)
        self._current = None

    def flush(self, total_presets, written, total, out_dir, conflicts=0):
        fc = self._counts["fallback"]
        xc = self._counts["fix"]
        wc = self._counts["warn"]

        parts = []
        if fc: parts.append(f"{fc} fallback{'s' if fc != 1 else ''}")
        if xc: parts.append(f"{xc} fix{'es' if xc != 1 else ''}")
        if wc: parts.append(f"{wc} warning{'s' if wc != 1 else ''}")

        print("────────────────────────────────────────")
        if parts:
            print(f"{total_presets} presets · {' · '.join(parts)}")
        else:
            print(f"{total_presets} presets · all clean")
        print()
        conflict_note = f" ({conflicts} name conflict(s) resolved)" if conflicts else ""
        print(f"written {written}/{total} .vital files to {out_dir}/{conflict_note}")

logger = PresetLogger()

def resolve_int_or_enum(val, default, enum_map_r, label):
    s = str(val).strip()
    if s.lstrip('-').replace('.','',1).isdigit():
        return safe_int(val, default)
    return fuzzy_lookup(s, enum_map_r, label)

# ---------------------------------------------------------------------------
# LEVENSHTEIN + TYPE INDEX for blob fallback
# ---------------------------------------------------------------------------

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j] + (ca != cb), prev[j+1] + 1, curr[j] + 1))
        prev = curr
    return prev[-1]


def get_type_prefix(id_str: str) -> str:
    for prefix in ("SAMPLES_STEREO", "AUDIO_FILE", "WAVE_DATA", "SAMPLES"):
        if id_str.startswith(prefix + "_"):
            return prefix
    return ""


def build_type_index(binary_map: dict) -> dict:
    index = {}
    for key in binary_map:
        p = get_type_prefix(key)
        if p:
            index.setdefault(p, []).append(key)
    return index


_TYPE_INDEX = {}


def closest_blob(blob_id: str, blob_map: dict) -> str:
    if not blob_id or blob_id == "_":
        return blob_id
    if blob_id in blob_map:
        return blob_map[blob_id]
    prefix = get_type_prefix(blob_id)
    candidates = _TYPE_INDEX.get(prefix, list(blob_map.keys()))
    if not candidates:
        return blob_id
    best = min(candidates, key=lambda k: levenshtein(blob_id, k))
    logger.log("fallback", f"{blob_id} → {best}")
    return blob_map[best]

def fuzzy_lookup(val, mapping, label="enum"):
    if val in mapping:
        return mapping[val]
    if not val or not mapping:
        if mapping is not None and not mapping:
            logger.log("warn", f"{label}: no style options for unknown model, defaulting to 0")
        return 0
    best = min(mapping.keys(), key=lambda k: levenshtein(val, k))
    best_dist = levenshtein(val, best)
    if best_dist <= 3:
        logger.log("fallback", f"{label}: '{val}' → '{best}'")
    return mapping[best]
    return 0

# ---------------------------------------------------------------------------
# EFFECT CHAIN
# ---------------------------------------------------------------------------

def encode_effect_order(names):
    # normalize to lowercase
    names = [n.lower().strip() for n in names]
    # aliases
    names = ["filterfx" if n in ("filter", "filter_fx", "filterfx") else n for n in names]
    names = ["distortion" if n in ("overdrive", "saturation") else n for n in names]
    names = ["compressor" if n in ("compression", "comp") else n for n in names]
    names = ["eq" if n in ("equalizer", "equaliser") else n for n in names]
    names = ["reverb" if n == "verb" else n for n in names]
    names = ["chorus" if n == "chor" else n for n in names]
    names = ["flanger" if n == "flng" else n for n in names]
    names = ["phaser" if n == "phas" else n for n in names]
    names = ["delay" if n == "dly" else n for n in names]
    
    # skip anything still unknown
    valid_names = [n for n in names if n in EFFECTS_BASE]
    if len(valid_names) != len(names):
        unknown = [n for n in names if n not in EFFECTS_BASE]
        logger.log("warn", f"unknown effects skipped: {unknown}")
    
    order = [EFFECTS_BASE.index(n) for n in valid_names]
    size = len(order)
    if size == 0:
        return 0.0
    code = 0
    for i in range(1, size):
        index = 0
        for j in range(i):
            index += order[i] < order[j]
        code *= i + 1
        code += index
    return float(code)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def safe_int(v, default=0):
    """Convert v to int, tolerating floats, float-strings, and unknown enum strings."""
    if isinstance(v, int):
        return v
    try:
        return int(float(v))
    except (ValueError, TypeError):
        import traceback
        traceback.print_stack(limit=6)
        logger.log("warn", f"safe_int: could not convert '{v}' to int, using {default}")
        return default

def strip_quotes(path):
    return path.strip().strip('"').strip("'")


def load_defaults():
    defaults_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defaults.json")
    with open(defaults_path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("settings", {})


ENV_KEYS = ["attack", "attack_power", "decay", "decay_power", "sustain", "release", "release_power", "hold", "delay"]
LFO_KEYS = ["frequency", "sync", "sync_type", "tempo", "phase", "smooth_mode", "smooth_time", "fade_time",
            "delay_time", "stereo", "keytrack_transpose", "keytrack_tune"]
LFO_SKIP = set(LFO_KEYS) | {"shape", "points_powers", "points", "powers", "smooth"}
RANDOM_KEYS = ["frequency", "sync", "sync_type", "tempo", "style", "stereo", "keytrack_transpose", "keytrack_tune"]


def take_rest(settings, prefix, block, used):
    for k, v in block.items():
        if k in used or v is True:
            continue
        f = try_float(v)
        if isinstance(f, float):
            settings[prefix + k] = f


def denormalize_dest(s):
    if s.startswith("macro_control"):
        return s
    return re.sub(r'([a-zA-Z])(\d+)', r'\1_\2', s)


def parse_block(line):
    line = line.strip().strip("{}")
    result = {}
    tokens = line.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if ":" in token:
            k, v = token.split(":", 1)
            # multi-token values: slurp until next key:value token
            if k in ("points_powers", "points", "powers"):
                vals = [v] if v else []
                i += 1
                while i < len(tokens) and ":" not in tokens[i]:
                    vals.append(tokens[i])
                    i += 1
                result[k] = " ".join(vals)
                continue
            else:
                result[k] = v
        else:
            result[token] = True
        i += 1
    return result


def parse_modulation_line(line):
    """Parse: source -> {dest:amount [bipolar:1] [stereo:1], dest:amount, ...}"""
    line = line.strip()
    arrow = line.find("->")
    if arrow == -1:
        return []
    source = line[:arrow].strip()
    rest = line[arrow+2:].strip().strip("{}")
    
    routes = []
    for entry in rest.split(","):
        entry = entry.strip()
        if not entry:
            continue
        tokens = entry.split()
        if not tokens:
            continue
        # first token is dest:amount
        dest_amount = tokens[0]
        if ":" not in dest_amount:
            continue
        dest, amount = dest_amount.split(":", 1)
        tags = {"amount": amount.rstrip(",;")}
        for token in tokens[1:]:
            if ":" in token:
                k, v = token.split(":", 1)
                tags[k] = v.rstrip(",;")
        routes.append((source, dest, tags))
    return routes


def try_float(v):
    try:
        # Strip commas, semicolons, or accidental whitespace
        if isinstance(v, str):
            v = v.rstrip(',;')
        return float(v)
    except (ValueError, TypeError):
        return v

SEMITONE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def parse_transpose_quantize(val):
    val = str(val).strip()
    if val == "none" or val == "0":
        return 0.0
    # already a raw number (e.g. "1551.0")
    if val.lstrip('-').replace('.','',1).isdigit():
        return float(val)
    result = 0
    for part in val.split("+"):
        part = part.strip()
        if part == "global":
            result |= (1 << 12)
        elif part in SEMITONE_NAMES:
            result |= (1 << SEMITONE_NAMES.index(part))
    return float(result)

# ---------------------------------------------------------------------------
# PARSE
# ---------------------------------------------------------------------------

def parse_idc(text, lfo_refmap=None, wt_refmap=None):
    presets = []
    blocks = re.split(r"(?m)^---[ \t]*$", text)
    for i, block in enumerate(blocks):
        block = block.strip()
        if not block:
            continue
        preset = parse_preset(block, index=i, lfo_refmap=lfo_refmap or {}, wt_refmap=wt_refmap or {})
        if "preset_name" not in preset:
            continue
        presets.append(preset)
    return presets


WAVETABLE_RANGE_MAP = {}
#_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wavetable_range_map.json")
#if os.path.exists(_map_path):
#    with open(_map_path, encoding="utf-8") as f:
#        WAVETABLE_RANGE_MAP = json.load(f)
#    print(f"loaded {len(WAVETABLE_RANGE_MAP)} wavetable ranges")

def closest_lfo(shape_id, lfo_refmap):
    if not shape_id or not lfo_refmap:
        return None
    if shape_id in lfo_refmap:
        return lfo_refmap[shape_id]
    best = min(lfo_refmap.keys(), key=lambda k: levenshtein(shape_id, k))
    best_dist = levenshtein(shape_id, best)
    if best_dist <= 3:
        logger.log("fallback", f"lfo: '{shape_id}' → '{best}'")
        return lfo_refmap[best]
    return None

def parse_lfo_shape(block, n, lfos_data, lfo_refmap):
    shape_id = block.get("shape", "Triangle")
    pp = closest_lfo(shape_id, lfo_refmap)
    if pp is not None:
        base_name = names.base_of(shape_id, pp, "lfo").replace("_", " ")
        points_raw = pp
    else:
        base_name = shape_id.replace("_", " ")
        points_raw = block.get("points_powers", "")

    flat_points = []
    flat_powers = []
    if points_raw:
        for token in str(points_raw).split():
            try:
                xy, power = token.split("|")
                x, y = xy.split(",")
                flat_points.extend([float(x), float(y)])
                flat_powers.append(float(power))
            except:
                pass
    num_points = len(flat_points) // 2
    while len(flat_powers) < num_points:
        flat_powers.append(0.0)
    lfos_data[n - 1] = {
        "name": base_name,
        "num_points": num_points,
        "points": flat_points,
        "powers": flat_powers,
        "smooth": False,
    }


def is_modulated(dest, modulations_list, mod_amounts, controlled_slots):
    for slot, mod in enumerate(modulations_list, 1):
        if mod.get("destination") != dest:
            continue
        amount = mod_amounts.get(slot, {}).get("amount", 0.0)
        if amount != 0.0:
            return True
        # zero amount — check if controlled by non-zero
        controller_amount = controlled_slots.get(slot)
        if controller_amount is not None and controller_amount != 0.0:
            return True
    return False

def closest_wt(wt_name, wt_refmap):
    if not wt_name or not wt_refmap:
        return None
    if wt_name in wt_refmap:
        return wt_refmap[wt_name]
    best = min(wt_refmap.keys(), key=lambda k: levenshtein(wt_name, k))
    best_dist = levenshtein(wt_name, best)
    if best_dist <= 3:
        logger.log("fallback", f"wt: '{wt_name}' → '{best}'")
        return wt_refmap[best]
    return None

def parse_preset(text, index=0, lfo_refmap=None, wt_refmap=None):
    if lfo_refmap is None: lfo_refmap = {}
    if wt_refmap is None: wt_refmap = {}
    logger.begin_preset(f"preset_{index + 1}")
    lines = [l for l in text.splitlines()]
    preset = {}
    settings = {}
    i = 0

    # header — @field value
    while i < len(lines):
        l = lines[i].strip()
        i += 1
        if not l or l.startswith("//") or l.startswith("#"):
            continue
        if not l.startswith("@"):
            i -= 1
            break
        rest = l[1:]
        parts = rest.split(" ", 1)
        key = parts[0]
        val = parts[1].strip() if len(parts) > 1 else "_"

        if key == "preset":
            preset["preset_name"] = val
            logger.rename_preset(val)
        elif key == "author":
            preset["author"] = "" if val == "_" else val
        elif key == "category":
            preset["preset_style"] = "" if val == "_" else val
        elif key == "style":
            pass
        elif key == "comments":
            preset["comments"] = "" if val == "_" else val
        elif key == "bpm":
            val_clean = re.sub(r'[^\d.\-]', '', val)
            try:
                settings["beats_per_minute"] = float(val_clean) / 60.0
            except ValueError:
                logger.log("warn", f"bpm: could not parse '{val}', using default")
                settings["beats_per_minute"] = 2.0  # 120 bpm default
        elif key.startswith("macro"):
            n = key[len("macro"):]
            preset[f"macro{n}"] = "" if val == "_" else val

    # body
    wavetables = [None, None, None]
    lfos_data = [dict(DEFAULT_LFO) for _ in range(8)]
    modulations_list = []
    mod_amounts = {}

    while i < len(lines):
        l = lines[i].strip()
        i += 1

        if not l or l.startswith("//") or l.startswith("#"):
            continue

        if l.startswith("effect_chain "):
            settings["effect_chain_order"] = encode_effect_order([n.strip() for n in l[len("effect_chain "):].split(">")])
            continue

        if l.startswith("voice"):
            brace_m = re.match(r'voice\s*(\{.*\})', l)
            if brace_m:
                block = parse_block(brace_m.group(1))
                voice_map = {
                    "polyphony": "polyphony",
                    "pitch_bend": "pitch_bend_range",
                    "velocity_track": "velocity_track",
                    "amplitude": "voice_amplitude",
                    "transpose": "voice_transpose",
                    "tune": "voice_tune",
                    "priority": "voice_priority",
                    "override": "voice_override",
                    "legato": "legato",
                    "portamento_time": "portamento_time",
                    "portamento_slope": "portamento_slope",
                    "portamento_force": "portamento_force",
                    "portamento_scale": "portamento_scale",
                    "oversampling": "oversampling",
                    "stereo_routing": "stereo_routing",
                    "stereo_mode": "stereo_mode",
                    "volume": "volume",
                }
                VOICE_ENUMS_R = {
                    "priority": VOICE_PRIORITY_R,
                    "override": VOICE_OVERRIDE_R,
                }
                for dc_key, vital_key in voice_map.items():
                    if dc_key in block:
                        val = block[dc_key]
                        if dc_key in VOICE_ENUMS_R:
                            settings[vital_key] = float(VOICE_ENUMS_R[dc_key].get(val, 0))
                        else:
                            settings[vital_key] = try_float(val)
                take_rest(settings, "", block, set(voice_map))
            if i < len(lines) and lines[i].strip().startswith("effect_chain "):
                chain_line = lines[i].strip()
                i += 1
                names_part = chain_line[len("effect_chain "):].strip()
                names = [n.strip() for n in names_part.split(">")]
                settings["effect_chain_order"] = encode_effect_order(names)
            continue

        # oscillators
        m = re.match(r'osc(\d)\s+(on|off)(.*)', l)
        if m:
            n = int(m.group(1))
            n = max(1, min(3, n))
            on = 1.0 if m.group(2) == "on" else 0.0
            settings[f"osc_{n}_on"] = on
            remainder = m.group(3).strip()
            if on and remainder.startswith("{"):
                block = parse_block(remainder)
                osc_keys = [
                    "level", "pan", "tune", "transpose", "transpose_quantize",
                    "wave_frame", "phase", "random_phase", "midi_track",
                    "unison_voices", "unison_detune", "unison_blend",
                    "detune_power", "detune_range", "stereo_spread",
                    "stack_style", "destination", "distortion_type",
                    "distortion_amount", "distortion_phase", "distortion_spread",
                    "spectral_morph_type", "spectral_morph_amount",
                    "spectral_morph_spread", "frame_spread", "spectral_unison",
                    "smooth_interpolation", "view_2d",
                ]
                OSC_ENUMS_R = {
                    "stack_style": STACK_STYLE_R,
                    "destination": OSC_DESTINATION_R,
                    "distortion_type": fill.osc_distortion_types(n),
                    "spectral_morph_type": SPECTRAL_MORPH_TYPE_R,
                }
                for k in osc_keys:
                    if k in block:
                        val = block[k]
                        if k in OSC_ENUMS_R:
                            settings[f"osc_{n}_{k}"] = float(OSC_ENUMS_R[k].get(val, DISTORTION_TYPE_R.get(val, 0) if k == "distortion_type" else 0))
                        elif k == "transpose_quantize":
                            settings[f"osc_{n}_{k}"] = parse_transpose_quantize(val)
                        else:
                            settings[f"osc_{n}_{k}"] = try_float(val)
                take_rest(settings, f"osc_{n}_", block, set(osc_keys))
            continue

        # wavetable
        m = re.match(r'wavetable(\d)(.*)', l)
        if m:
            n = int(m.group(1)) - 1
            n = max(0, min(2, n))
            wt = {"author": "", "version": SYNTH_VERSION, "groups": [{"components": []}]}
            remainder = m.group(2).strip()
            all_braces = re.findall(r'\{[^}]*\}', remainder)
            if all_braces:
                meta = parse_block(all_braces[0])
                stored = closest_wt(meta.get("name", ""), wt_refmap) if meta.get("type") in ("synthesized", "wave_source") and "data" not in meta else None
                if isinstance(stored, dict):
                    wavetables[n] = json.loads(json.dumps(stored))
                    continue
                wt["name"] = meta.get("name", "_").replace("_", " ")
                comp_type = meta.get("type", "wave_source")
                if comp_type == "empty":
                    wt["full_normalize"] = meta.get("normalize", "1") != "0"
                    wt["remove_all_dc"] = meta.get("remove_dc", "1") != "0"
                    wavetables[n] = wt
                    continue

                wt["full_normalize"] = meta.get("normalize", "1") != "0"
                wt["remove_all_dc"] = meta.get("remove_dc", "1") != "0"

                if comp_type == "synthesized":
                    comps_hint = []
                    if "components" in meta:
                        comps_hint = [c.replace("_", " ") for c in str(meta["components"]).split(",")]

                    wt_name = meta.get("name", "_")
                    frames_str = frames_str = closest_wt(wt_name, wt_refmap)
                    if frames_str:
                        # expand from refmap into synth_frames
                        synth_frames = []
                        for frame_line in frames_str.splitlines():
                            inner = frame_line.strip()[1:-1]  # strip { }
                            parts = inner.split(None, 1)
                            pos = float(parts[0].split("_", 1)[1])
                            rest = parts[1] if len(parts) > 1 else ""
                            fields = parse_block("{" + rest + "}")
                            synth_frames.append((pos, fields))
                    else:
                        synth_frames = []
                        while i < len(lines):
                            pl = lines[i].strip()
                            if not pl.startswith("{position_"):
                                break
                            inner = pl[1:-1]
                            parts = inner.split(None, 1)
                            pos = float(parts[0].split("_", 1)[1])
                            rest = parts[1] if len(parts) > 1 else ""
                            fields = parse_block("{" + rest + "}")
                            synth_frames.append((pos, fields))
                            i += 1

                    wt["groups"][0]["components"] = reconstruct_synthesized_wavetable(
                        synth_frames, comps_hint or None
                    )

                elif comp_type == "audio_file":
                    comp = {
                        "type": "Audio File Source",
                        **{k: safe_int(meta[k], 1) for k in ("interpolation", "interpolation_style") if k in meta},
                        "audio_file": meta.get("data", "_"),
                        "audio_sample_rate": safe_int(meta.get("sample_rate", 44100), 44100),
                        "fade_style":  resolve_int_or_enum(meta.get("fade_style",  1), 1, FADE_STYLE_R,  "fade_style"),
                        "phase_style": resolve_int_or_enum(meta.get("phase_style", 0), 0, PHASE_STYLE_R, "phase_style"),
                        "random_seed": safe_int(meta.get("seed", 0), 0),
                        "window_size": try_float(meta.get("window_size", 2048)),
                        "keyframes": []
                    }
                    comp["normalize_gain"] = meta.get("normalize_gain") == "1"
                    comp["normalize_mult"] = meta.get("normalize_mult") == "1"
                    while i < len(lines) and lines[i].strip().startswith("{keyframe"):
                        kf_block = parse_block(lines[i].strip())
                        i += 1
                        norm_pos = float(kf_block.get("pos", 0))
                        real_range = WAVETABLE_RANGE_MAP.get(meta.get("data", ""), 128)
                        real_pos = round(norm_pos / 100 * real_range)
                        comp["keyframes"].append({
                            "position": real_pos,
                            "start_position": try_float(kf_block.get("start", 0)),
                            "window_fade": try_float(kf_block.get("window_fade", 1.0)),
                            "window_size": try_float(kf_block.get("window_size", 2048.0)),
                        })
                    wt["groups"][0]["components"] = [comp]

                else:
                    keyframes = []
                    wt_name = meta.get("name", "_")

                    if "data" in meta:
                        # single frame inline — always takes priority
                        keyframes.append({"position": 0, "wave_data": meta.get("data", "_")})
                    else:
                        frames_str = frames_str = closest_wt(wt_name, wt_refmap)
                        if frames_str:
                            for frame_line in frames_str.splitlines():
                                inner = frame_line.strip()[1:-1]
                                parts = inner.split(None, 1)
                                pos = float(parts[0].split("_", 1)[1])
                                rest = parts[1] if len(parts) > 1 else ""
                                wave_id = rest.strip().split()[0] if rest.strip() else "_"
                                keyframes.append({"position": pos, "wave_data": wave_id})
                        else:
                            while i < len(lines):
                                pl = lines[i].strip()
                                if not pl.startswith("{position_"):
                                    break
                                inner = pl[1:-1]
                                parts = inner.split(None, 1)
                                pos = float(parts[0].split("_", 1)[1])
                                rest = parts[1] if len(parts) > 1 else ""
                                wave_id = rest.strip().split()[0] if rest.strip() else "_"
                                keyframes.append({"position": pos, "wave_data": wave_id})
                                i += 1
                    comp = {
                        "type": "Wave Source",
                        **{k: safe_int(meta[k], 1) for k in ("interpolation", "interpolation_style") if k in meta},
                        "keyframes": keyframes
                    }
                    wt["groups"][0]["components"] = [comp]

            wavetables[n] = wt
            continue

        # sample
        m = re.match(r'sample\s+(on|off)(.*)', l)
        if m:
            on = 1.0 if m.group(1) == "on" else 0.0
            settings["sample_on"] = on
            remainder = m.group(2).strip()
            if on and remainder.startswith("{"):
                block = parse_block(remainder)
                sample_obj = {
                    "name": block.get("name", "_").replace("_", " "),
                    "samples": block.get("data", "_"),
                    "length": safe_int(block.get("length", 44100), 44100),
                    "sample_rate": safe_int(block.get("sample_rate", 44100), 44100),
                }
                if "stereo" in block:
                    sample_obj["samples_stereo"] = block["stereo"]
                settings["sample"] = sample_obj
                sample_keys = ["level", "pan", "tune", "transpose", "transpose_quantize", "keytrack", "loop", "bounce", "random_phase", "destination"]
                for k in sample_keys:
                    if k in block:
                        if k == "transpose_quantize":
                            settings[f"sample_{k}"] = parse_transpose_quantize(block[k])
                        elif k == "destination":
                            settings[f"sample_{k}"] = float(resolve_int_or_enum(block[k], 3, OSC_DESTINATION_R, "sample_destination"))
                        else:
                            settings[f"sample_{k}"] = try_float(block[k])
                take_rest(settings, "sample_", block, set(sample_keys) | {"name", "data", "length", "sample_rate", "stereo"})
            continue

        # filters — filter1, filter2, filterfx
        m = re.match(r'filter(1|2|fx)\s+(on|off)(.*)', l)
        if m:
            fn = m.group(1)
            on = 1.0 if m.group(2) == "on" else 0.0
            prefix = f"filter_{fn}_" if fn != "fx" else "filter_fx_"
            settings[f"{prefix}on"] = on
            remainder = m.group(3).strip()
            if on and remainder.startswith("{"):
                block = parse_block(remainder)
                filter_keys = ["cutoff", "resonance", "drive", "mix",
                               "blend", "blend_transpose", "keytrack", "filter_input",
                               "formant_resonance", "formant_spread", "formant_transpose",
                               "formant_x", "formant_y"]
                for k in filter_keys:
                    if k in block:
                        settings[f"{prefix}{k}"] = try_float(block[k])
                take_rest(settings, prefix, block, set(filter_keys) | {"model", "style"})
                if "model" in block:
                    model_str = block["model"]
                    settings[f"{prefix}model"] = float(fuzzy_lookup(model_str, FILTER_MODEL_R, "filter_model"))
                    if "style" in block:
                        style_map_r = FILTER_STYLE_R.get(model_str, {})
                        settings[f"{prefix}style"] = float(fuzzy_lookup(block["style"], style_map_r, "filter_style"))
            continue

        m = re.match(r'((?:(?:lfo|env|random)?\d+\s*,\s*)+(?:lfo|env|random)?\d+)(.*)', l) if re.match(r'(lfo|env|random)\d', l) else None
        if m:
            block_type = re.match(r'(lfo|env|random)', l).group(1)
            indices = [int(x) for x in re.findall(r'\d+', m.group(1))]
            remainder = m.group(2).strip()
            all_braces = re.findall(r'\{[^}]*\}', remainder)

            for n in indices:
                if block_type == "env":
                    n = max(1, min(6, n))
                    if all_braces:
                        block = parse_block(all_braces[0])
                        for k in ENV_KEYS:
                            if k in block:
                                settings[f"env_{n}_{k}"] = try_float(block[k])
                        take_rest(settings, f"env_{n}_", block, set(ENV_KEYS))

                elif block_type == "lfo":
                    n = max(1, min(8, n))
                    if all_braces:
                        block = parse_block(all_braces[0])
                        for k in LFO_KEYS:
                            if k in block:
                                settings[f"lfo_{n}_{k}"] = try_float(block[k])
                        take_rest(settings, f"lfo_{n}_", block, LFO_SKIP)
                        if "shape" in block:
                            parse_lfo_shape(block, n, lfos_data, lfo_refmap)
                    if len(all_braces) > 1:
                        shape_block = parse_block(all_braces[1])
                        parse_lfo_shape(shape_block, n, lfos_data, lfo_refmap)
                    if all_braces and parse_block(all_braces[0]).get("smooth") == "1":
                        lfos_data[n - 1]["smooth"] = True

                elif block_type == "random":
                    n = max(1, min(4, n))
                    if all_braces:
                        block = parse_block(all_braces[0])
                        for k in RANDOM_KEYS:
                            if k in block:
                                settings[f"random_{n}_{k}"] = try_float(block[k])
                        take_rest(settings, f"random_{n}_", block, set(RANDOM_KEYS))
            continue

        # envelopes
        m = re.match(r'env(\d)(.*)', l)
        if m:
            n = int(m.group(1))
            n = max(1, min(6, n))
            remainder = m.group(2).strip()
            if remainder.startswith("{"):
                block = parse_block(remainder)
                for k in ENV_KEYS:
                    if k in block:
                        settings[f"env_{n}_{k}"] = try_float(block[k])
                take_rest(settings, f"env_{n}_", block, set(ENV_KEYS))
            continue

        m = re.match(r'lfo(\d)(.*)', l)
        if m:
            n = max(1, min(8, int(m.group(1))))
            remainder = m.group(2).strip()
            brace_start = remainder.find("{")
            if brace_start != -1:
                block = parse_block(remainder[brace_start:])
                for k in LFO_KEYS:
                    if k in block:
                        settings[f"lfo_{n}_{k}"] = try_float(block[k])
                take_rest(settings, f"lfo_{n}_", block, LFO_SKIP)
                if "shape" in block:
                    parse_lfo_shape(block, n, lfos_data, lfo_refmap)
                if block.get("smooth") == "1":
                    lfos_data[n - 1]["smooth"] = True
            all_braces = re.findall(r'\{[^}]*\}', remainder)
            if len(all_braces) > 1:
                shape_block = parse_block(all_braces[1])
                parse_lfo_shape(shape_block, n, lfos_data, lfo_refmap)
                if brace_start != -1 and parse_block(all_braces[0]).get("smooth") == "1":
                    lfos_data[n - 1]["smooth"] = True
            continue

        m = re.match(r'random(\d)(.*)', l)
        if m:
            n = int(m.group(1))
            n = max(1, min(4, n))
            remainder = m.group(2).strip()
            if remainder.startswith("{"):
                block = parse_block(remainder)
                for k in RANDOM_KEYS:
                    if k in block:
                        settings[f"random_{n}_{k}"] = try_float(block[k])
                take_rest(settings, f"random_{n}_", block, set(RANDOM_KEYS))
            continue

        # effects
        effect_map = {
            "filterfx": ("filter_fx_", ["cutoff", "resonance",
                         "drive", "mix", "blend", "blend_transpose", "keytrack",
                         "formant_resonance", "formant_spread", "formant_transpose",
                         "formant_x", "formant_y"]),
            "distortion": ("distortion_", ["drive", "mix", "filter_blend",
                           "filter_cutoff", "filter_resonance"]),
            "chorus": ("chorus_", ["voices", "frequency", "tempo", "sync", "cutoff",
                       "spread", "delay_1", "delay_2", "feedback", "mod_depth", "dry_wet"]),
            "compressor": ("compressor_", ["attack", "release", "mix",
                           "low_lower_threshold", "low_upper_threshold",
                           "low_lower_ratio", "low_upper_ratio", "low_gain",
                           "band_lower_threshold", "band_upper_threshold",
                           "band_lower_ratio", "band_upper_ratio", "band_gain",
                           "high_lower_threshold", "high_upper_threshold",
                           "high_lower_ratio", "high_upper_ratio", "high_gain"]),
            "delay": ("delay_", ["frequency", "tempo", "sync",
                      "aux_frequency", "aux_tempo", "aux_sync",
                      "feedback", "filter_cutoff", "filter_spread", "dry_wet"]),
            "eq": ("eq_", ["low_cutoff", "low_resonance", "low_gain", "low_mode",
                   "band_cutoff", "band_resonance", "band_gain", "band_mode",
                   "high_cutoff", "high_resonance", "high_gain", "high_mode"]),
            "flanger": ("flanger_", ["frequency", "tempo", "sync", "center",
                        "feedback", "mod_depth", "phase_offset", "dry_wet"]),
            "phaser": ("phaser_", ["frequency", "tempo", "sync", "center",
                       "feedback", "blend", "mod_depth", "phase_offset", "dry_wet"]),
            "reverb": ("reverb_", ["decay_time", "size", "delay", "dry_wet",
                       "chorus_amount", "chorus_frequency",
                       "pre_low_cutoff", "pre_high_cutoff",
                       "low_shelf_cutoff", "low_shelf_gain",
                       "high_shelf_cutoff", "high_shelf_gain"]),
        }
        matched_effect = None
        for ename, (eprefix, ekeys) in effect_map.items():
            if l.startswith(ename + " "):
                matched_effect = (ename, eprefix, ekeys)
                break
        if matched_effect:
            ename, eprefix, ekeys = matched_effect
            brace_idx = l.find("{")
            tokens = (l[:brace_idx] if brace_idx != -1 else l).split()
            if len(tokens) < 2:
                settings[f"{eprefix}on"] = 0.0
                continue
            on = 1.0 if tokens[1] == "on" else 0.0
            settings[f"{eprefix}on"] = on
            if on and brace_idx != -1:
                block = parse_block(l[brace_idx:])
                for k in ekeys:
                    if k in block:
                        settings[f"{eprefix}{k}"] = try_float(block[k])
                take_rest(settings, eprefix, block, set(ekeys) | {"type", "filter_order", "model", "style", "enabled_bands"})
                if ename == "distortion":
                    if "type" in block:
                        settings["distortion_type"] = float(fuzzy_lookup(block["type"], DISTORTION_FX_TYPE_R, "distortion_type"))
                    if "filter_order" in block:
                        settings["distortion_filter_order"] = float(fuzzy_lookup(block["filter_order"], DISTORTION_FX_FILTER_ORDER_R, "distortion_filter_order"))
                elif ename == "filterfx":
                    if "model" in block:
                        model_str = block["model"]
                        settings["filter_fx_model"] = float(fuzzy_lookup(model_str, FILTER_MODEL_R, "filter_model"))
                        if "style" in block:
                            style_map_r = FILTER_STYLE_R.get(model_str, {})
                            settings["filter_fx_style"] = float(fuzzy_lookup(block["style"], style_map_r, "filter_style"))
                elif ename == "compressor":
                    if "enabled_bands" in block:
                        settings["compressor_enabled_bands"] = float(fuzzy_lookup(block["enabled_bands"], COMPRESSOR_BANDS_R, "compressor_bands"))
                elif ename == "delay":
                    if "style" in block:
                        settings["delay_style"] = float(fuzzy_lookup(block["style"], DELAY_STYLE_R, "delay_style"))
            continue

        # modulations
        if l == "modulations":
            slot = 1
            while i < len(lines):
                ml = lines[i].strip()
                i += 1
                if not ml or ml.startswith("//") or ml.startswith("#"):
                    continue
                if ml == "_":
                    break
                if "->" in ml:
                    label = re.match(r'^\[(\d+)\]', ml)
                    if label and 1 <= int(label.group(1)) <= 64 and int(label.group(1)) >= slot:
                        slot = int(label.group(1))
                    clean = re.sub(r'^\[\d+\]\s*', '', ml)
                    for source, destination, tags in parse_modulation_line(clean):
                        vital_source = denormalize_dest(source)
                        vital_dest = denormalize_dest(destination)
                        while len(modulations_list) < slot - 1:
                            modulations_list.append({"source": "", "destination": ""})
                        modulations_list.append({
                            "source": vital_source,
                            "destination": vital_dest,
                        })
                        mod_amounts[slot] = {
                            "amount": try_float(tags.get("amount") or 0.0),
                            "bipolar": try_float(tags.get("bipolar") or 0.0),
                            "stereo": try_float(tags.get("stereo") or 0.0),
                            "bypass": try_float(tags.get("bypass") or 0.0),
                            "power": try_float(tags.get("power") or 0.0),
                        }
                        slot += 1
                    continue
                mc = re.match(r'macro_control_(\d)\s+([\d.\-]+)', ml)
                if mc:
                    settings[f"macro_control_{mc.group(1)}"] = float(mc.group(2))
                    continue
                if ml.startswith("mod_wheel"):
                    settings["mod_wheel"] = float(ml.split()[1])
                    continue
                # truly unknown line, stop modulations block
                break
            continue


    # =========================================================================
    # PHASE 2 — COMPONENT ACTIVITY
    # =========================================================================

    routed_sources = {mod.get("source", "") for mod in modulations_list}
    # routed_sources is used by graph building and any future phase 2 checks

    # =========================================================================
    # PHASE 3 — MODULATION GRAPH
    # =========================================================================

    def build_graph(mod_list, mod_amts):
        """
        Returns:
          graph[slot] = {
            "source": str, "destination": str, "amount": float,
            "children": [slot, ...],   # slots that have this slot's amount as dest
            "parents":  [slot, ...],   # slots that control this slot's amount
          }
        """
        graph = {}
        for slot, mod in enumerate(mod_list, 1):
            src = mod.get("source", "")
            dst = mod.get("destination", "")
            amt = mod_amts.get(slot, {}).get("amount", 0.0)
            graph[slot] = {
                "source": src,
                "destination": dst,
                "amount": amt,
                "children": [],
                "parents": [],
            }
        # wire parent/child relationships
        for slot, node in graph.items():
            m = re.match(r"modulation_(\d+)_amount", node["destination"])
            if m:
                target = int(m.group(1))
                if target in graph:
                    graph[target]["parents"].append(slot)
                    node["children"].append(target)
        return graph

    graph = build_graph(modulations_list, mod_amounts)

    def get_chain_roots(slot, graph, visited=None):
        """Walk up parent chain, return all root sources (no parents)."""
        if visited is None:
            visited = set()
        if slot in visited:
            return set()
        visited.add(slot)
        node = graph.get(slot)
        if not node:
            return set()
        if not node["parents"]:
            return {node["source"]}
        roots = set()
        for p in node["parents"]:
            roots |= get_chain_roots(p, graph, visited)
        return roots

    def chain_has_nonzero_amount(slot, graph, visited=None):
        """
        Returns True if this slot OR any ancestor in the chain
        has a non-zero amount.
        """
        if visited is None:
            visited = set()
        if slot in visited:
            return False
        visited.add(slot)
        node = graph.get(slot)
        if not node:
            return False
        if node["amount"] != 0.0:
            return True
        for p in node["parents"]:
            if chain_has_nonzero_amount(p, graph, visited):
                return True
        return False

    USER_ROOTS = {"mod_wheel"} | {f"macro_control_{n}" for n in range(1, 5)}

    def is_user_controlled(slot, graph):
        """True if any root of this slot's chain is a macro or mod_wheel."""
        return bool(get_chain_roots(slot, graph) & USER_ROOTS)

    def build_pinned_set(graph):
        """
        Slots whose slot-number is referenced as modulation_N_amount
        anywhere in the graph. Transitive.
        """
        pinned = set()
        changed = True
        while changed:
            changed = False
            for slot, node in graph.items():
                m = re.match(r"modulation_(\d+)_amount", node["destination"])
                if m:
                    t = int(m.group(1))
                    if t not in pinned:
                        pinned.add(t)
                        changed = True
        return pinned

    pinned = build_pinned_set(graph)

    def is_effectively_modulated(dest, graph):
        """
        Returns True if dest has any slot pointing to it where
        the full chain can deliver non-zero (user-controlled or non-zero amount).
        """
        for slot, node in graph.items():
            if node["destination"] != dest:
                continue
            if is_user_controlled(slot, graph):
                return True
            if chain_has_nonzero_amount(slot, graph):
                return True
        return False

    # =========================================================================
    # PHASE 4 — EFFECT-SPECIFIC CHECKS
    # =========================================================================

    COMPRESSOR_BAND_GAINS = {
        "multiband":   ["compressor_low_gain", "compressor_band_gain", "compressor_high_gain"],
        "low_band":    ["compressor_low_gain", "compressor_band_gain"],
        "high_band":   ["compressor_band_gain", "compressor_high_gain"],
        "single_band": ["compressor_band_gain"],
    }

    if settings.get("distortion_on", 0.0) == 1.0:
        drive_dead = (settings.get("distortion_drive", 1.0) == -30.0
                      and not is_effectively_modulated("distortion_drive", graph))
        mix_dead   = (settings.get("distortion_mix", 1.0) == 1.0
                      and not is_effectively_modulated("distortion_mix", graph))
        if drive_dead and mix_dead:
            settings["distortion_on"] = 0.0
            logger.log("fix", "distortion disabled — drive min + mix 1 + no modulation")

    if settings.get("compressor_on", 0.0) == 1.0:
        enabled = COMPRESSOR_BANDS.get(
            int(settings.get("compressor_enabled_bands", 0)), "multiband")
        gain_keys = COMPRESSOR_BAND_GAINS[enabled]
        all_min  = all(settings.get(k, 0.0) == -30.0 for k in gain_keys)
        no_mods  = (all(not is_effectively_modulated(k, graph) for k in gain_keys)
                    and not is_effectively_modulated("compressor_mix", graph))
        mix_full = settings.get("compressor_mix", 0.0) == 1.0
        if all_min and mix_full and no_mods:
            settings["compressor_on"] = 0.0
            logger.log("fix", "compressor disabled — all active band gains min + mix 1 + no modulation")

    if settings.get("filter_fx_on", 0.0) == 1.0:
        if (settings.get("filter_fx_mix", 1.0) == 0.0
                and not is_effectively_modulated("filter_fx_mix", graph)):
            settings["filter_fx_on"] = 0.0
            logger.log("fix", "filterfx disabled — mix 0 + no modulation")

    if settings.get("chorus_on", 0.0) == 1.0:
        if (settings.get("chorus_dry_wet", 1.0) == 0.0
                and not is_effectively_modulated("chorus_dry_wet", graph)):
            settings["chorus_on"] = 0.0
            logger.log("fix", "chorus disabled — dry_wet 0 + no modulation")

    if settings.get("delay_on", 0.0) == 1.0:
        if (settings.get("delay_dry_wet", 1.0) == 0.0
                and not is_effectively_modulated("delay_dry_wet", graph)):
            settings["delay_on"] = 0.0
            logger.log("fix", "delay disabled — dry_wet 0 + no modulation")

    if settings.get("flanger_on", 0.0) == 1.0:
        if (settings.get("flanger_dry_wet", 1.0) == 0.0
                and not is_effectively_modulated("flanger_dry_wet", graph)):
            settings["flanger_on"] = 0.0
            logger.log("fix", "flanger disabled — dry_wet 0 + no modulation")

    if settings.get("phaser_on", 0.0) == 1.0:
        if (settings.get("phaser_dry_wet", 1.0) == 0.0
                and not is_effectively_modulated("phaser_dry_wet", graph)):
            settings["phaser_on"] = 0.0
            logger.log("fix", "phaser disabled — dry_wet 0 + no modulation")

    if settings.get("reverb_on", 0.0) == 1.0:
        if (settings.get("reverb_dry_wet", 1.0) == 0.0
                and not is_effectively_modulated("reverb_dry_wet", graph)):
            settings["reverb_on"] = 0.0
            logger.log("fix", "reverb disabled — dry_wet 0 + no modulation")

    # =========================================================================
    # PHASE 5 — FILTER ROUTING
    # =========================================================================

    f1_on  = settings.get("filter_1_on",  0.0) == 1.0
    f2_on  = settings.get("filter_2_on",  0.0) == 1.0
    ffx_on = settings.get("filter_fx_on", 0.0) == 1.0

    DEST_FILTER1   = 0.0
    DEST_FILTER2   = 1.0
    DEST_FILTER1_2 = 2.0
    DEST_EFFECTS   = 3.0
    DEST_DIRECT    = 4.0

    DEST_NAMES = {
        0.0: "filter1", 1.0: "filter2", 2.0: "filter1+2",
        3.0: "effects", 4.0: "direct_out"
    }

    def reroute_destination(key, label, settings):
        dest = settings.get(key)
        if dest is None:
            return
        dest = float(dest)
        new_dest = None

        if dest == DEST_FILTER1 and not f1_on:
            new_dest = DEST_EFFECTS
        elif dest == DEST_FILTER2 and not f2_on:
            new_dest = DEST_EFFECTS
        elif dest == DEST_FILTER1_2:
            if not f1_on and not f2_on:
                new_dest = DEST_EFFECTS
            elif not f1_on:
                new_dest = DEST_FILTER2
            elif not f2_on:
                new_dest = DEST_FILTER1

        if new_dest is not None and new_dest != dest:
            settings[key] = new_dest
            logger.log("fix", f"{label} destination {DEST_NAMES[dest]} → {DEST_NAMES[new_dest]}")

    for n in range(1, 4):
        if settings.get(f"osc_{n}_on", 0.0) == 1.0:
            reroute_destination(f"osc_{n}_destination", f"osc{n}", settings)
    if settings.get("sample_on", 0.0) == 1.0:
        reroute_destination("sample_destination", "sample", settings)

    # fatal check — all filters off, something still routed to a filter
    if not f1_on and not f2_on and not ffx_on:
        for n in range(1, 4):
            if settings.get(f"osc_{n}_on", 0.0) != 1.0:
                continue
            dest = float(settings.get(f"osc_{n}_destination", 4.0))
            if dest in (DEST_FILTER1, DEST_FILTER2, DEST_FILTER1_2):
                settings[f"osc_{n}_destination"] = DEST_EFFECTS
                logger.log("fix", f"osc{n} destination → effects (all filters off)")
        if settings.get("sample_on", 0.0) == 1.0:
            dest = float(settings.get("sample_destination", 4.0))
            if dest in (DEST_FILTER1, DEST_FILTER2, DEST_FILTER1_2):
                settings["sample_destination"] = DEST_EFFECTS
                logger.log("fix", "sample destination → effects (all filters off)")

    # =========================================================================
    # PHASE 6 — OSC/SAMPLE PARAM CLEANUP
    # =========================================================================

    defaults = load_defaults()

    for n in range(1, 4):
        if settings.get(f"osc_{n}_on", 0.0) != 1.0:
            continue
        prefix = f"osc_{n}_"

        # distortion_type none → reset amount/phase/spread to init
        if settings.get(f"{prefix}distortion_type", 0.0) == 0.0:
            for param in ["distortion_amount", "distortion_phase", "distortion_spread"]:
                init_val = defaults.get(f"{prefix}{param}")
                if init_val is not None:
                    settings[f"{prefix}{param}"] = init_val

        # spectral_morph_type none → reset amount/spread to init
        if settings.get(f"{prefix}spectral_morph_type", 0.0) == 0.0:
            for param in ["spectral_morph_amount", "spectral_morph_spread"]:
                init_val = defaults.get(f"{prefix}{param}")
                if init_val is not None:
                    settings[f"{prefix}{param}"] = init_val

    # always-audible check
    def source_is_always_audible(n_or_sample, settings, graph):
        if n_or_sample == "sample":
            lvl_key = "sample_level"
            on_key  = "sample_on"
        else:
            lvl_key = f"osc_{n_or_sample}_level"
            on_key  = f"osc_{n_or_sample}_on"
        if settings.get(on_key, 0.0) != 1.0:
            return False
        lvl = settings.get(lvl_key, 0.0)
        if lvl != 0.0:
            return True
        return is_effectively_modulated(lvl_key, graph)

    sources = [1, 2, 3, "sample"]
    always_audible = any(source_is_always_audible(s, settings, graph) for s in sources)

    feeding = fill.fm_sources(settings)
    for n in range(1, 4):
        if settings.get(f"osc_{n}_on", 0.0) != 1.0 or n in feeding:
            continue
        lvl_key = f"osc_{n}_level"
        if settings.get(lvl_key, 0.0) != 0.0:
            continue
        if is_effectively_modulated(lvl_key, graph):
            continue
        settings[f"osc_{n}_on"] = 0.0
        logger.log("fix", f"osc{n} disabled — level 0, no effective modulation")

    if settings.get("sample_on", 0.0) == 1.0 and "sample" not in feeding:
        if settings.get("sample_level", 0.0) == 0.0:
            if not is_effectively_modulated("sample_level", graph):
                settings["sample_on"] = 0.0
                logger.log("fix", "sample disabled — level 0, no effective modulation")

    # =========================================================================
    # PHASE 7 — MODULATION PRUNING
    # =========================================================================

    # classify each slot
    def slot_keep(slot, graph, settings, pinned):
        node = graph.get(slot)
        if not node:
            return False
        src = node["source"]
        dst = node["destination"]
        if not src or not dst:
            return False

        # pinned — must stay regardless
        if slot in pinned:
            return True

        # destination component off → discard
        on_key = None
        for prefix, ok in DEST_PREFIX_MAP.items():
            if dst.startswith(prefix):
                on_key = ok
                break
        if on_key and settings.get(on_key, 0.0) == 0.0:
            return False

        # user-controlled chain → keep even at zero amount
        if is_user_controlled(slot, graph):
            return True

        # non-zero amount reachable anywhere in chain → keep
        if chain_has_nonzero_amount(slot, graph):
            return True

        return False

    pruned = []
    for slot, mod in enumerate(modulations_list, 1):
        src = mod.get("source", "")
        dst = mod.get("destination", "")
        if not src or not dst:
            pruned.append({"source": "", "destination": ""})
            continue
        if slot_keep(slot, graph, settings, pinned):
            pruned.append(mod)
        else:
            logger.log("fix", f"mod slot {slot} dropped — {src} → {dst}")
            pruned.append({"source": "", "destination": ""})

    # pad to 64
    while len(pruned) < 64:
        pruned.append({"source": "", "destination": ""})

    # compact — move filled non-pinned slots forward into gaps
    free_positions = [i for i, mod in enumerate(pruned) if (i + 1) not in pinned]
    movable = [(i, mod) for i, mod in enumerate(pruned)
               if (i + 1) not in pinned
               and mod.get("source") and mod.get("destination")]

    compacted = list(pruned)
    moved = 0
    for new_i, (old_i, mod) in zip(free_positions, movable):
        compacted[new_i] = mod
        old_slot, new_slot = old_i + 1, new_i + 1
        if old_slot != new_slot and old_slot in mod_amounts:
            mod_amounts[new_slot] = mod_amounts.pop(old_slot)
            moved += 1

    for pos in free_positions[len(movable):]:
        compacted[pos] = {"source": "", "destination": ""}

    if moved:
        logger.log("fix", f"compacted {moved} modulation slot(s)")

# =========================================================================
# PHASE 8 — WRITEOUT
# =========================================================================

    for n in range(1, 65):
        mod = mod_amounts.get(n, {})
        settings[f"modulation_{n}_amount"]  = mod.get("amount",  0.0)
        settings[f"modulation_{n}_bipolar"] = mod.get("bipolar", 0.0)
        settings[f"modulation_{n}_stereo"]  = mod.get("stereo",  0.0)
        settings[f"modulation_{n}_bypass"]  = mod.get("bypass",  0.0)
        settings[f"modulation_{n}_power"]   = mod.get("power",   0.0)

    settings["modulations"] = compacted
    settings["lfos"] = lfos_data
    settings["custom_warps"] = [dict(DEFAULT_WARP) for _ in range(3)]
    settings["random_values"] = [dict(DEFAULT_RANDOM_VALUE) for _ in range(3)]

    settings["wavetables"] = [
        wt if wt is not None else {
            "author": "", "full_normalize": True, "remove_all_dc": True,
            "version": SYNTH_VERSION, "name": "Init",
            "groups": [{"components": []}]
        }
        for wt in wavetables
    ]

    for k, v in fill.HARDCODED.items():
        settings.setdefault(k, v)
    for k, v in defaults.items():
        if k not in settings and not isinstance(v, (list, dict)):
            settings[k] = v

    if "sample" not in settings:
        settings["sample"] = {"length": 44100, "name": "", "sample_rate": 44100, "samples": ""}
    if "sample_on" not in settings:
        settings["sample_on"] = 0.0

    preset["settings"] = settings
    preset["synth_version"] = SYNTH_VERSION
    logger.end_preset()
    return preset


# ---------------------------------------------------------------------------
# BLOB DECODE
# ---------------------------------------------------------------------------

def decode_blobs(settings, blob_map):
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("wave_data", "audio_file") and isinstance(v, str) and (v in blob_map or re.match(r"^(WAVE_DATA|AUDIO_FILE|SAMPLES_STEREO|SAMPLES)_[a-z]+(_[a-z]+)+$", v)):
                    x[k] = closest_blob(v, blob_map)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(settings.get("wavetables", []))
    sample = settings.get("sample", {})
    if sample:
        sample["samples"] = closest_blob(sample.get("samples", ""), blob_map)
        if "samples_stereo" in sample:
            sample["samples_stereo"] = closest_blob(sample["samples_stereo"], blob_map)


# ---------------------------------------------------------------------------
# PROCESS ONE FILE
# ---------------------------------------------------------------------------

def process_file(path, blob_map, lfo_refmap, wt_refmap):
    logger.reset()
    if not os.path.exists(path):
        print(f"file not found: {path}")
        return

    with open(path, encoding="utf-8") as f:
        text = f.read()

    presets = parse_idc(text, lfo_refmap, wt_refmap)
    print(f"parsed {len(presets)} presets")

    input_base = os.path.splitext(path)[0]
    stem = os.path.basename(input_base)
    if OUTPUT_NEXT_TO_INPUT:
        root_dir = os.path.dirname(input_base)
    else:
        root_dir = os.path.dirname(os.path.abspath(__file__))

    if RESTORE_VITAL:
        if USE_SUBFOLDER:
            out_dir = os.path.join(root_dir, stem + "_vital")
        else:
            out_dir = os.path.join(root_dir, "restored")
        os.makedirs(out_dir, exist_ok=True)
        seen_names = {}
        written = 0
        conflicts = 0
        for i, preset in enumerate(presets):
            decode_blobs(preset.get("settings", {}), blob_map)
            name = preset.get("preset_name", f"preset_{i+1}")
            safe_name = re.sub(r'[\\/:*?"<>|]', '', name)  # strip illegal chars
            safe_name = re.sub(r'[\s\-_]+', '_', safe_name).strip('_')  # collapse separators
            if not safe_name:
                from datetime import datetime
                safe_name = f"preset_{datetime.now().strftime('%H%M')}"

            # deduplicate within this batch
            count = seen_names.get(safe_name, 0)
            seen_names[safe_name] = count + 1
            candidate = f"{safe_name}_{count}" if count > 0 else safe_name

            # avoid overwriting files from any previous run
            out_path = os.path.join(out_dir, f"{candidate}.vital")
            suffix = count
            while os.path.exists(out_path):
                suffix += 1
                out_path = os.path.join(out_dir, f"{safe_name}_{suffix}.vital")
                conflicts += 1

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(preset, f)
            written += 1

        logger.flush(len(presets), written, len(presets), out_dir, conflicts)

    else:
        out_path = os.path.join(root_dir, (stem + "_restored.jsonl") if USE_SUBFOLDER else (stem + ".jsonl"))
        # for jsonl, append rather than overwrite if file already exists
        mode = "a" if os.path.exists(out_path) else "w"
        with open(out_path, mode, encoding="utf-8") as f:
            for preset in presets:
                decode_blobs(preset.get("settings", {}), blob_map)
                f.write(json.dumps(preset) + "\n")
        action = "appended to" if mode == "a" else "written"
        print(f"{action} {len(presets)} presets → {out_path}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def _watch_default(blob_map, lfo_refmap, wt_refmap):
    """Background thread: re-runs process_file whenever DEFAULT_FILE is saved."""
    watch_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_FILE)
    if not os.path.exists(watch_path):
        return  # file doesn't exist yet, nothing to watch
    last_mtime = os.path.getmtime(watch_path)
    while True:
        time.sleep(0.5)
        try:
            mtime = os.path.getmtime(watch_path)
        except FileNotFoundError:
            continue
        if mtime != last_mtime:
            last_mtime = mtime
            print(f"\n  ⟳ detected change in {DEFAULT_FILE}, auto-converting...")
            process_file(watch_path, blob_map, lfo_refmap, wt_refmap)
            print("  ⟳ done\n", end="", flush=True)


def main():
    global _TYPE_INDEX, DEBUG_LOG, WAVETABLE_RANGE_MAP
    if "--debug" in sys.argv:
        DEBUG_LOG = True
    logger.mode = "debug" if DEBUG_LOG else "normal"

    # try master first, fall back to local flat map
    master_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_map_master.json")
    flat_path   = r"reference_map_wordpair.json"

    if os.path.exists(master_path):
        map_path = master_path
        print(f"using master map: {master_path}")
    elif os.path.exists(flat_path):
        map_path = flat_path
        print(f"master not found — falling back to: {flat_path}")
    else:
        print(f"no reference map found — checked:\n  {master_path}\n  {flat_path}")
        return

    with open(map_path, encoding="utf-8") as f:
        map_data = json.load(f)

    lfo_refmap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "full_presets_merged_lfo_refmap.json")
    if os.path.exists(lfo_refmap_path):
        with open(lfo_refmap_path, encoding="utf-8") as f:
            lfo_refmap = json.load(f)
        print(f"lfo refmap: {len(lfo_refmap)} shape IDs loaded")
    else:
        lfo_refmap = {}
        print("lfo refmap: not found, shapes will restore as-is")

    wt_refmap_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "full_presets_merged_wt_refmap.json")
    if os.path.exists(wt_refmap_path):
        with open(wt_refmap_path, encoding="utf-8") as f:
            wt_refmap = json.load(f)
        print(f"wt refmap: {len(wt_refmap)} wavetable IDs loaded")
    else:
        wt_refmap = {}
        print("wt refmap: not found, wavetables will restore as-is")

    # normalize to flat name->blob for closest_blob
    if "names" in map_data and "blobs" in map_data:
        print(f"two-object format detected")
        blob_map = {}
        missing = 0
        for name, h in map_data["names"].items():
            if h not in map_data["blobs"]:
                print(f"  WARNING: '{name}' points to missing hash '{h}' — skipped")
                missing += 1
                continue
            blob_map[name] = map_data["blobs"][h]
        if missing:
            print(f"  {missing} dangling name(s) skipped")
        print(f"loaded {len(blob_map)} name->blob entries ({len(map_data['blobs'])} unique blobs)")
    else:
        blob_map = map_data
        print(f"flat format detected")
        print(f"loaded {len(blob_map)} entries")

    _TYPE_INDEX = build_type_index(blob_map)
    print(f"type index: { {k: len(v) for k, v in _TYPE_INDEX.items()} }")

    # build wavetable range map from master frames
    if "frames" in map_data and "names" in map_data:
        WAVETABLE_RANGE_MAP = {
            name: map_data["frames"][h]
            for name, h in map_data["names"].items()
            if h in map_data["frames"]
        }
        print(f"wavetable range map: {len(WAVETABLE_RANGE_MAP)} entries from master")
    else:
        # fallback to external file
        _map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wavetable_range_map.json")
        if os.path.exists(_map_path):
            with open(_map_path, encoding="utf-8") as f:
                WAVETABLE_RANGE_MAP = json.load(f)
            print(f"wavetable range map: {len(WAVETABLE_RANGE_MAP)} entries from file")
        else:
            WAVETABLE_RANGE_MAP = {}
            print(f"wavetable range map: empty — no master frames or external file found")

    # start file watcher for DEFAULT_FILE in background
    watcher = threading.Thread(target=_watch_default, args=(blob_map, lfo_refmap, wt_refmap), daemon=True)
    watcher.start()

    print("ready — enter a file path or 'q' to quit\n")

    # ── persistent loop ────────────────────────────────────────────────────
    while True:
        try:
            raw = input("file path: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nexiting")
            break

        if raw.lower() in ("q", "quit", "exit"):
            print("exiting")
            break

        if not raw:
            raw = os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_FILE)
            print(f"  → using default: {raw}")

        path = strip_quotes(raw)
        process_file(path, blob_map, lfo_refmap, wt_refmap)
        print()   # blank line between runs


if __name__ == "__main__":
    main()