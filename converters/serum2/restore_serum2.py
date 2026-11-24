#!/usr/bin/env python3
"""
restore_serum2.py
=================
.idc  ->  Serum 2 .json  ->  .SerumPreset

This is the half that meets the model. serum2_to_idc.py runs once over the
corpus to make training data; this runs on every preset the 9B ever emits, and
a 9B trained on ~6k examples is approximately right and never exactly right.

So it is NOT a parser. A parser's job is to reject bad input. This one's job is
to take something close and produce something Serum loads. Almost none of the
code below is parsing. See why.md, and scripts/restore_idc_fixer2.py, which is
the design this follows.

Two rules it never breaks:

  1. init.json is the scaffold, ALWAYS. Every preset starts as a deep copy of a
     fresh Serum instance. Nothing is built from nothing, so a key the .idc
     never mentions sits at the plugin's own default rather than at a number
     this script invented. Serum omits any parameter equal to its default, so
     writing only what we have is exactly what Serum itself does.

  2. Nothing is ever silently dropped. Every fallback, repair and discard is
     counted and named.

Phases, in order. Each is separately defensible and separately loggable:

  1 PARSE       tolerant, line by line. Never raises. An unreadable line is
                counted, not fatal.
  2 RESOLVE     every name through a vocabulary with Levenshtein fallback:
                params, enum members, mod sources, destinations, wavetable
                paths, LFO shapes, blob ids.
  3 MOD MAP     which destinations are modulated, by what, and whether the
                source is player input. Serum has no amount-chaining (measured:
                0 of 52,229 slots target another ModSlot), so this is one level
                deep, unlike Vital's graph.
  4 EFFECTS     an effect that cannot be heard is turned off rather than left
                claiming to be on.
  5 ROUTING     a channel routed into a filter that is off gets rerouted.
  6 SOURCES     an oscillator at zero volume with nothing modulating it.
  7 MOD PRUNE   drop slots that cannot do anything, compact the rest.
  8 WRITEOUT    merge onto the scaffold, rebuild metadata, pack.

Interactive when run with no arguments.
"""

import json, os, re, sys, copy, struct, argparse, pathlib, difflib
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.environ.get("DEAFTONES_CACHE", os.path.join(HERE, "..", "..", "cache")), "serum2")
BLOB_MAP = os.path.join(CACHE, "blobs.jsonl")

# ---- toggles ---------------------------------------------------------------
WRITE_SERUMPRESET = True    # also pack a loadable .SerumPreset beside the json
OUTPUT_NEXT_TO_INPUT = True # output beside the .idc, not beside this script
INLINE_BLOBS = True         # embed wavetable data; without it Serum names a
                            # table it does not carry, and that looks normal
FUZZ_LIMIT = 3              # max edit distance accepted on a name lookup

# Off by default. On, every fallback, fix, correction and drop is appended to
# restore_events.log as one tab-separated row:
#
#   <preset_index>\t<preset_name>\t<kind>\t<key>\t<detail>
#
# `key` matches the string serum2_to_idc.py writes for the same decision, so
# the two logs join on (preset, key). A verdict present in one and absent from
# the other is the two sides disagreeing, which is what these are for.
EVENT_LOG = False
EVENT_LOG_PATH = os.path.join(CACHE, "restore_events.log")
# ----------------------------------------------------------------------------

MAGIC = b"XferJson\x00"

# Standalone: nothing is imported from serum2_to_idc.py. Every table duplicated
# from it is marked, and if the writer's spelling changes these change with it.

# Source ids 0-59, measured off Serum's UI from generated presets.
# serum2_schema.md 5; provenance serum2_idc.md 9.15 / 9.17.
SOURCE_IDS = {
    0: "off", 1: "mod_wheel",
    2: "env1", 3: "env2", 4: "env3", 5: "env4",
    16: "velo", 17: "note", 18: "aftertouch", 19: "poly_aftertouch",
    20: "noise_osc", 21: "noteon_rand1", 22: "noteon_rand2",
    23: "noteon_alt", 24: "noteon_alt2", 33: "pitch_bend",
    34: "expr_x", 35: "expr_y", 36: "expr_z", 37: "release_velo",
    38: "fixed", 49: "osc_a", 50: "osc_b", 51: "osc_c", 52: "sub_osc",
    53: "filter1", 54: "filter2", 55: "active_voices",
    56: "voice_mod1", 57: "voice_mod2", 58: "voice_index",
    59: "noteon_rand_discrete",
}
for _i in range(10):
    SOURCE_IDS[6 + _i] = f"lfo{_i + 1}"
    SOURCE_IDS[39 + _i] = f"lfo{_i + 1}_y"
for _i in range(8):
    SOURCE_IDS[25 + _i] = f"macro{_i + 1}"
NAME_TO_SOURCE = {v: k for k, v in SOURCE_IDS.items()}

# Player input. A slot fed from one of these is kept even at zero amount,
# because the performer supplies the amount at play time and a zero there is a
# starting position, not a dead route.
USER_SOURCES = ({1, 16, 17, 18, 19, 33, 34, 35, 36, 37}
                | set(range(25, 33)))

# Out-of-range source ids clamp to 59 inside Serum rather than erroring, so a
# writer has to range-check itself. serum2_schema.md 5.
MAX_SOURCE_ID = 59

MOD_MODULE = {
    "Oscillator": "osc", "WTOsc": "osc", "GranularOsc": "osc",
    "MultiSampleOsc": "osc", "SampleOsc": "osc", "SpectralOsc": "osc",
    "SubOsc": "sub", "NoiseOsc": "noise",
    "VoiceFilter": "filter", "Env": "env", "LFO": "lfo", "Macro": "macro",
    "Global": "global", "RoutingSlot": "routing", "Filter": "filter",
    "LFOPointModBus": "lfopointmodbus",
}

OSC_TYPE_OUT = {"wt": None, "sample": "kOsc_Sample", "spectral": "kOsc_Spectral",
                "multisample": "kOsc_MultiSample", "granular": "kOsc_Granular"}
ENGINE_CHILD = {"wt": "WTOsc", "sample": "SampleOsc", "spectral": "SpectralOsc",
                "multisample": "MultiSampleOsc", "granular": "GranularOsc"}

# RoutingSlot0..6, one per mixer channel strip. 0/1/2 = OSC A/B/C and 5 =
# FILTER 1 are confirmed across two presets; 3/4/6 are NOISE/SUB/FILTER 2 in an
# order that is NOT confirmed, so they keep their slot numbers.
ROUTING_SLOTS = ["osc_a", "osc_b", "osc_c", "slot3", "slot4", "filter1", "slot6"]

ROUTING_DEST = {"master": "kRoutingDestMaster", "filter": "kRoutingDestFilter",
                "direct": "kRoutingDestDirect", "none": "kRoutingDestNone"}

SECTION_ALIASES = {
    "oscillator": "osc", "wt": "wavetable", "table": "wavetable",
    "envelope": "env", "modulation": "modulations", "mod": "modulations",
    "fx_chain": "effect_chain", "chain": "effect_chain",
    "compressor": "comp", "distort": "distortion", "verb": "reverb",
    "equalizer": "eq", "flange": "flanger", "phase": "phaser",
    "filter_fx": "filterfx", "hyperdim": "hyper", "hyper_d": "hyper",
}

OFF_MARKERS = ("off", "unrouted", "none", "disabled", "_")

_SNAKE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_2 = re.compile(r"([a-z0-9])([A-Z])")
_SNAKE_STRIP = re.compile(r"^k(?:Param|UIParam|RoutingDest|Routing)?")
_SNAKE_SEP = re.compile(r"[-\s./]+")


def snake(s):
    """Duplicated from serum2_to_idc.py. Needed here to recognise the writer's
    fallback spelling for a destination module type."""
    s = _SNAKE_STRIP.sub("", str(s))
    s = _SNAKE_SEP.sub("_", s)
    s = _SNAKE_1.sub(r"\1_\2", s)
    s = _SNAKE_2.sub(r"\1_\2", s)
    return re.sub(r"_+", "_", s).lower().strip("_") or "_"


# Effects in FX racks 1 and 2 are written with an r2_ / r3_ prefix, because
# destModuleID on an FX is a position within its own rack and rack 0 is the
# chain. Without this they parse as unknown sections and the whole effect is
# lost, which is what 3 presets in 250 did.
RACK_PREFIX = re.compile(r"^r(\d+)_(.+)$")

# Singleton modules whose module id is a fixed non-zero number. Oscillator3 is
# NOISE and Oscillator4 is SUB (serum2_schema.md 4), so a modulation naming the
# NoiseOsc type carries id 3, measured on all 349 rows in the corpus that
# target it. The writer drops the id for these because there is only one, and
# defaulting it to 0 on the way back silently retargets the modulation.
FIXED_MODULE_ID = {"NoiseOsc": 3, "SubOsc": 4}

# destModuleID on an FX appears to encode the rack in its hundreds digit:
# rack = id // 100, slot = id % 100. INFERRED, not measured off the plugin.
# The evidence is five rows in the corpus (FXDistortion[100], FXDistortion[201]
# x2, FXFilter[200]) landing exactly where FXRack1 and FXRack2 are non-empty,
# in 3 presets each. It matters because the bounds guard would otherwise throw
# those five away as out of range.
FX_RACK_STRIDE = 100


# =============================================================================
# LOGGER
# =============================================================================

class PresetLogger:
    """Per-preset, like restore_idc_fixer2's. A run that reports 'all clean' is
    a run where nothing was guessed."""

    KINDS = ("fallback", "fix", "warn", "drop")

    def __init__(self):
        self.reset()

    def reset(self):
        self.counts = Counter()
        self.detail = Counter()
        self.current = None
        self.printed = False
        self.per_preset = []
        self.events = []
        self.idx = 0

    def begin(self, name, idx=0):
        self.current = (name, [])
        self.idx = idx
        self.printed = False

    def rename(self, name):
        if self.current:
            self.current = (name, self.current[1])

    def log(self, kind, msg):
        self.counts[kind] += 1
        self.detail[f"{kind}: {msg}"] += 1
        if self.current is not None:
            self.current[1].append((kind, msg))
        self.event(kind, "", msg)

    def event(self, kind, key, detail=""):
        """One decision, joinable against the writer's log on (preset, key)."""
        if EVENT_LOG:
            name = self.current[0] if self.current else ""
            self.events.append((self.idx, name, kind, key, detail))

    def write_events(self, path):
        if not EVENT_LOG or not self.events:
            return 0
        with open(path, "w", encoding="utf-8") as f:
            f.write("# idx\tpreset\tkind\tkey\tdetail\n")
            for idx, name, kind, key, detail in self.events:
                f.write(f"{idx}\t{name}\t{kind}\t{key}\t{detail}\n")
        return len(self.events)

    def end(self):
        if self.current and self.current[1]:
            self.per_preset.append(self.current)
        self.current = None

    def summary(self):
        if not self.counts:
            return "all clean"
        return " · ".join(f"{self.counts[k]} {k}" for k in self.KINDS
                          if self.counts[k])

    def report(self, limit=60):
        out = [f"summary: {self.summary()}", ""]
        for k, n in self.detail.most_common(limit):
            out.append(f"  {n:>7}  {k}")
        return "\n".join(out)


LOG = PresetLogger()


# =============================================================================
# FUZZY LOOKUP
# =============================================================================

def levenshtein(a, b):
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j] + (ca != cb), prev[j + 1] + 1, cur[j] + 1))
        prev = cur
    return prev[-1]


def closest(key, candidates, label, limit=None):
    """Exact hit, else nearest inside `limit`, else None. Never silent.

    The identifiers in .idc are word-shaped precisely so edit distance means
    something here: a model gets a name slightly wrong far more often than it
    invents one from nothing."""
    if key in candidates:
        return candidates[key] if isinstance(candidates, dict) else key
    if not candidates:
        return None
    limit = FUZZ_LIMIT if limit is None else limit
    keys = list(candidates)
    best = min(keys, key=lambda k: levenshtein(key, k))
    d = levenshtein(key, best)
    if d <= limit:
        LOG.log("fallback", f"{label}: {key!r} -> {best!r} (d={d})")
        return candidates[best] if isinstance(candidates, dict) else best
    # last resort: a prefix/substring match catches truncations that edit
    # distance scores badly on, e.g. a long name cut short mid-token
    near = difflib.get_close_matches(key, keys, n=1, cutoff=0.75)
    if near:
        LOG.log("fallback", f"{label}: {key!r} ~> {near[0]!r} (ratio)")
        return candidates[near[0]] if isinstance(candidates, dict) else near[0]
    LOG.log("drop", f"{label}: {key!r} unresolved")
    return None


def try_float(v, default=None):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().rstrip(",;"))
    except (TypeError, ValueError):
        return default


# =============================================================================
# VOCABULARY
# =============================================================================

class Vocab:
    """Everything the restorer resolves against. Built from the corpus by
    build_reverse_map.py rather than hand-written, because the writer's snake()
    is lossy and a hand inverse drifts the first time either side changes."""

    # The writer flattens an oscillator and its mode child onto one line, so a
    # key on `noise` may belong to NoiseOsc and a key on `sub` to SubOsc. The
    # frame tells us which, so the flattening is undone here rather than
    # requiring a format change.
    FLAT = {
        "Oscillator3": ["NoiseOsc", "Oscillator"],
        "Oscillator4": ["SubOsc", "Oscillator"],
    }
    ALT = {"Oscillator": ["Oscillator", "NoiseOsc", "SubOsc", "WTOsc"]}

    def __init__(self, path, shapes=None, blobs=None):
        d = json.load(open(path, encoding="utf-8"))
        self.param = d["param"]
        self.enum = d["enum"]
        self.dest = d["dest"]
        self.fx = d["fx"]
        self.engine = d.get("engine_tag", {})
        self.frame = d.get("frame", {})
        self.wtpath = d.get("wtpath", {})
        self.noisepath = d.get("noisepath", {})
        self.irpath = d.get("irpath", {})
        local = os.path.join(CACHE, "paths.json")
        if os.path.exists(local):
            got = json.load(open(local, encoding="utf-8"))
            for kind in ("wtpath", "noisepath", "irpath"):
                table = getattr(self, kind)
                for k, v in got.get(kind, {}).items():
                    table.setdefault(k, v)
        self.shapes = shapes or {}
        self.blobs = blobs or {}
        self.blob_index = defaultdict(list)
        for b in self.blobs:
            self.blob_index[b.split("_")[0]].append(b)
        self.mod_base = defaultdict(list)
        for t, base in MOD_MODULE.items():
            self.mod_base[base].append(t)
        # family that owns each kParam, for splitting a flattened line
        self.owner = {}
        for fam, keys in self.frame.items():
            for k in keys:
                self.owner.setdefault(k, []).append(fam)

    def ir_path(self, form):
        hit = self.irpath.get(form)
        if hit:
            return hit if isinstance(hit, str) else hit[0]
        return form.replace("_", " ")


    def kparam(self, family, key):
        """-> (kParamName, owning family). The family matters because the enum
        vocabulary is keyed on it, so resolving a NoiseOsc key under Oscillator
        would look its value up in the wrong vocabulary."""
        fams = self.ALT.get(family, [family])
        for f in fams:
            if key in self.param.get(f, {}):
                return self.param[f][key], f
        merged = {}
        for f in reversed(fams):
            merged.update(self.param.get(f, {}))
        kp = closest(key, merged, f"param[{family}]")
        return kp, family

    def split_family(self, module_key, kp, fallback):
        """Which module actually owns this kParam, for the flattened lines."""
        cands = self.FLAT.get(module_key)
        if not cands:
            return fallback
        for fam in cands:
            if kp in self.frame.get(fam, ()):
                return fam
        return fallback

    def value(self, family, kp, raw):
        vocab = self.enum.get(f"{family}.{kp}")
        if vocab:
            v = closest(str(raw), vocab, f"enum[{family}.{kp}]")
            if v is not None:
                return v
            LOG.log("warn", f"enum[{family}.{kp}]: keeping {raw!r} verbatim")
            return str(raw)
        f = try_float(raw)
        if f is None:
            LOG.log("warn", f"{family}.{kp}: {raw!r} is not a number")
            return raw
        return f

    # -- modulation ----------------------------------------------------------

    def source_id(self, name):
        name = (name or "").strip()
        if not name or name in ("_", "off", "none"):
            return 0
        v = closest(name, NAME_TO_SOURCE, "source")
        if v is None:
            return 0
        if not 0 <= v <= MAX_SOURCE_ID:
            LOG.log("fix", f"source id {v} out of range, clamped to {MAX_SOURCE_ID}")
            return MAX_SOURCE_ID
        return v

    def resolve_dest(self, dest, fxmap):
        """fxmap is {idc effect label -> (rack, slot)}, built from the effect
        lines actually parsed. A flat rack-0 list cannot answer this: the
        writer labels rack 1 and 2 effects r2_/r3_ and their destModuleID is
        rack*100 + slot, so the rack has to come back out of the name."""
        """'osc1_table_pos' -> (typeString, moduleID, paramName, paramID)

        Ambiguity is real: `freq` alone belongs to six module types. The idc
        prefix is what separates them, which is why the prefix is part of the
        spelling and not decoration."""
        best = None
        for pn in self.dest:
            if dest.endswith("_" + pn) and (best is None or len(pn) > len(best)):
                best = pn
        if best is None:
            # the whole token may be a param with no prefix, or misspelled
            pn = closest(dest, self.dest, "dest-param")
            if pn is None:
                return None
            cands = pn
            prefix = ""
        else:
            cands = self.dest[best]
            prefix = dest[: -(len(best) + 1)]

        m = re.match(r"^([a-z_]+?)(\d*)$", prefix)
        base, idx = (m.group(1), m.group(2)) if m else (prefix, "")

        if prefix and prefix in fxmap:
            rack, slot = fxmap[prefix]
            rm = RACK_PREFIX.match(prefix)
            cls = self.fx.get(rm.group(2) if rm else prefix, [None])[0]
            for ts, pid, pn in cands:
                if ts == cls:
                    return ts, rack * FX_RACK_STRIDE + slot, pn, pid
        wanted = self.mod_base.get(base, [])
        for ts, pid, pn in cands:
            if ts in wanted:
                # Singleton modules still have a module id, and it is not 0.
                # NoiseOsc is Oscillator3, so its id is 3 in all 349 rows that
                # target it. The writer drops the id for these because there is
                # only one of each, so it has to be put back from knowledge.
                if ts in FIXED_MODULE_ID:
                    return ts, FIXED_MODULE_ID[ts], pn, pid
                mid = (int(idx) - 1) if idx else 0
                return ts, max(mid, 0), pn, pid

        # The writer falls back to snake(destModuleTypeString) when the target
        # is not in rack 0's chain, so the prefix IS the type name: FXDistortion
        # writes fx_distortion. Checking that turns a coin flip into a match.
        for ts, pid, pn in cands:
            if snake(ts) == prefix or snake(ts) == base:
                mid = (int(idx) - 1) if idx else 0
                LOG.log("fallback", f"dest[{dest}]: matched {ts} by type name")
                return ts, max(mid, 0), pn, pid

        ts, pid, pn = cands[0]
        LOG.log("drop", f"dest[{dest}]: no match, guessed {ts} from "
                        f"{sorted({c[0] for c in cands})}")
        mid = (int(idx) - 1) if idx else 0
        return ts, max(mid, 0), pn, pid

    # -- payloads ------------------------------------------------------------

    def wavetable_path(self, name, noise=False):
        """The writer strips a leading slash and turns spaces into underscores,
        and 2170 real paths contain a literal underscore, so the form is not
        invertible. It is looked up, never reconstructed. 158 of 288 distinct
        paths do not survive a naive inverse."""
        table = self.noisepath if noise else self.wtpath
        v = closest(name, table, "wtpath" if not noise else "noisepath", limit=10 ** 6)
        if v is None:
            guess = "/" + name.replace("_", " ")
            LOG.log("warn", f"path {name!r} not in vocabulary, guessed {guess!r}")
            return guess
        return v[0] if isinstance(v, list) else v

    def blob(self, bid):
        """Type-partitioned, so a WAVE_DATA can never resolve to a SAMPLES."""
        if not bid or bid == "_":
            return None
        if bid in self.blobs:
            return self.blobs[bid]
        pref = bid.split("_")[0]
        pool = self.blob_index.get(pref) or list(self.blobs)
        if not pool:
            return None
        best = min(pool, key=lambda k: levenshtein(bid, k))
        d = levenshtein(bid, best)
        if d <= 6:
            LOG.log("fallback", f"blob: {bid!r} -> {best!r} (d={d})")
            return self.blobs[best]
        LOG.log("drop", f"blob: {bid!r} unresolved")
        return None

    def shape(self, sid):
        if not sid or sid in ("custom", "_"):
            return None
        return closest(sid, self.shapes, "lfo shape")


# =============================================================================
# PHASE 1 - TOLERANT PARSING
# =============================================================================

MULTI_VALUE = ("points", "flex", "pp")


def parse_block(text):
    """'{a:1 b:c points:x,y|c x,y|c}' -> dict.

    Tolerant on purpose: unbalanced braces, a bare token, a trailing comma and
    a missing value are all survivable. The model will produce all of them."""
    if text is None:
        return {}
    text = text.strip()
    text = text[1:] if text.startswith("{") else text
    text = text[:-1] if text.endswith("}") else text
    toks = text.replace("}", " ").replace("{", " ").split()
    out, i = {}, 0
    while i < len(toks):
        t = toks[i]
        if ":" in t:
            k, v = t.split(":", 1)
            k = k.strip().lstrip(",")
            if k in MULTI_VALUE:
                vals = [v] if v else []
                i += 1
                while i < len(toks) and ":" not in toks[i]:
                    vals.append(toks[i])
                    i += 1
                out[k] = " ".join(vals)
                continue
            out[k] = v.rstrip(",;")
        elif t not in ("_",):
            out[t] = True
        i += 1
    return out


def braces(s):
    return re.findall(r"\{[^{}]*\}?", s) if "{" in s else []


def split_presets(text):
    blocks, cur = [], []
    for line in text.splitlines():
        if line.strip() == "---":
            if cur:
                blocks.append(cur)
            cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    blocks = [b for b in blocks if any(l.strip() for l in b)]

    # A block with no @preset is not a preset, it is the preamble of the one
    # after it.
    #
    # The model emits its own `---` after the opening <vision> span, because
    # in the training data that is where a delimiter sits. So every preset
    # carrying a vision block arrives SPLIT IN TWO: a prose fragment, then
    # the real preset. The fragment restored against the bare scaffold and
    # took its name, which is where init.json / init_1 / init_2 came from.
    #
    # Measured 2026-08-23 across three runs: presets_kbog6 (no vision spans)
    # had exactly 2 delimiters per preset and was clean; z6nip, 48f1c and
    # s59ce had 3 per preset and produced one junk file per preset, 60 of
    # them. Counting delimiters against @preset lines is the cheap check.
    #
    # Merging rather than dropping matters: the fragment can hold an
    # unclosed <think>, and parse_preset's span tracking recovers on the
    # first closing tag, so the preamble has to reach the same parse as the
    # body it belongs to.
    merged, pending = [], []
    for b in blocks:
        if any(l.lstrip().startswith("@preset") for l in b):
            merged.append(pending + b)
            pending = []
        else:
            pending += b
    if pending:
        LOG.log("drop", f"trailing block with no @preset line, "
                        f"{len(pending)} line(s) discarded")
    return merged


def parse_points(s):
    """'x,y|c x,y|c' -> (xs, ys, cs). '@ID ID ID' -> the three blob ids."""
    s = (s or "").strip()
    if s.startswith("@"):
        ids = s[1:].split()
        while len(ids) < 3:
            ids.append(ids[-1] if ids else "")
        return ids[0], ids[1], ids[2]
    xs, ys, cs = [], [], []
    for tok in s.split():
        xy, _, c = tok.partition("|")
        x, _, y = xy.partition(",")
        fx, fy = try_float(x), try_float(y)
        if fx is None or fy is None:
            LOG.log("drop", f"curve point {tok!r}")
            continue
        xs.append(fx)
        ys.append(fy)
        cs.append(try_float(c, 0.5))
    return xs, ys, cs


def curve_object(points, loop=None, vocab=None):
    xs, ys, cs = parse_points(points)
    if isinstance(xs, str):
        obj = {"xVals": xs, "yVals": ys, "curveVals": cs}
        n = blob_lengths()[0].get(xs, -1)
        if n > 0:
            obj["numPoints"] = max(n - 1, 1)
    else:
        if not xs:
            return None
        obj = {"xVals": xs, "yVals": ys, "curveVals": cs,
               "numPoints": max(len(xs) - 1, 1)}
    lp = try_float(loop)
    if lp is not None:
        obj["loopbackPointNum"] = int(lp)
    return obj


# =============================================================================
# PARSE ONE PRESET
# =============================================================================

class Preset:
    """Everything read out of one .idc block, before any repair."""

    def __init__(self):
        self.meta = {}
        self.modules = defaultdict(dict)     # module key -> kParam -> value
        self.children = defaultdict(dict)    # module key -> child key -> dict
        self.engines = {}                    # osc index -> engine tag
        self.fx = []                         # ordered [(name, cls, params)]
        self.fx_order = None
        self.mods = []                       # dicts, pre-prune
        self.routing = {}
        self.macros = {}
        self.curves = {}                     # module key -> curveData
        self.off = set()                     # modules the idc marked off
        self.scalars = {}

    def setp(self, mod, kp, val):
        self.modules[mod][kp] = val


def parse_preset(lines, vocab, index=0):
    P = Preset()
    LOG.begin(f"preset_{index + 1}", index + 1)
    i, last_wt = 0, None
    span = None

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1
        if not line or line.startswith(("//", "#")):
            continue
        merged = re.match(r"^(lfo\d+\s*)\{([^{}]*?)\s*(shape:custom\b[^{}]*)\}\s*$", line)
        if merged and merged.group(2).strip():
            line = f"{merged.group(1)}{{{merged.group(2).strip()}}} {{{merged.group(3).strip()}}}"
            LOG.log("fix", f"{merged.group(1).strip()}: custom shape split into its own block")

        # ---- reasoning spans ----------------------------------------------
        # The model interleaves <plan>/<check>/<consolidate>/<crux> commentary
        # THROUGH the preset body, not just at the top, so this cannot be
        # handled by trimming a prefix. It is prose, and prose parses as .idc
        # by accident: measured 2026-08-23, the line beginning 'The raw
        # multi-oscillator sequence...' fuzzy-matched section 'the' -> 'lfo'
        # in all 26 presets of one run. It landed on n=0, which no lfo branch
        # accepts, so it was dropped rather than applied -- luck, not design.
        # A span sentence starting 'Filter1' or 'Voice' would have been
        # applied as parameters.
        sm = re.match(r"^<(/?)([a-z_]+)>$", line)
        if sm:
            span = None if sm.group(1) else sm.group(2)
            continue
        if span is not None:
            continue

        # ---- header ------------------------------------------------------
        if line.startswith("@"):
            key, _, val = line[1:].partition(" ")
            val = val.strip()
            key = key.strip().lower()
            if key == "preset":
                P.meta["presetName"] = val.replace("_", " ")
                LOG.rename(P.meta["presetName"])
            elif key == "author":
                P.meta["presetAuthor"] = "" if val == "_" else val.replace("_", " ")
            elif key == "comments":
                P.meta["presetDescription"] = "" if val == "_" else val
            elif key == "tags":
                P.meta["tags"] = ([] if val == "_" else
                                  [w.replace("_", "-").title() for w in val.split()])
            elif key.startswith("macro"):
                pass                                   # names, not values
            elif key == "category":
                pass
            else:
                LOG.log("warn", f"unknown header @{key}")
            continue

        # ---- effect chain -------------------------------------------------
        if line.startswith("effect_chain"):
            rest = line.split(None, 1)[1].strip() if " " in line else "_"
            P.fx_order = ([] if rest == "_"
                          else [n.strip() for n in rest.split(">") if n.strip()])
            continue

        # ---- wavetable flex continuation ----------------------------------
        if line.startswith("{flex"):
            pts = line[5:].strip().rstrip("}").strip()
            if last_wt is not None:
                P.children[f"Oscillator{last_wt}"]["_flex"] = pts
            else:
                LOG.log("drop", "flex line with no wavetable above it")
            continue

        # ---- modulation matrix --------------------------------------------
        if line.split()[0] in ("modulations", "modulation", "mod"):
            while i < len(lines):
                ml = lines[i].strip()
                if not ml:
                    i += 1
                    continue
                if ml == "_":
                    i += 1
                    break
                if ml.startswith(("//", "#")):
                    i += 1
                    continue
                if not ml.startswith("["):
                    break
                i += 1
                m = re.match(r"^\[\s*\d+\s*\]\s*(.+?)\s*->\s*(.*)$", ml)
                if not m:
                    LOG.log("drop", f"mod row: {ml[:48]!r}")
                    continue
                srcs = [s.strip() for s in m.group(1).split("+") if s.strip()]
                blk = parse_block(m.group(2))
                P.mods.append({"srcs": srcs, "blk": blk})
            continue

        sc = re.match(r"^(velo|note)_curve\s+(\{.*)$", line)
        if sc:
            P.scalars[sc.group(1)] = parse_block(sc.group(2))
            continue

        # ---- macro values --------------------------------------------------
        mc = re.match(r"^macro_control_(\d+)\s+(\S+)$", line)
        if mc:
            v = try_float(mc.group(2), 0.0)
            if v:
                P.macros[int(mc.group(1))] = v
            continue

        # ---- generic 'name [state] {block}' --------------------------------
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)"
                     r"\s*(.*)$", line)
        if not m:
            LOG.log("drop", f"line: {line[:48]!r}")
            continue
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
        rest = m.group(2).strip()

        first = rest.split()[0].lower() if rest else "_"
        is_off = first in OFF_MARKERS and not rest.startswith("{")
        blks = braces(rest)
        head = parse_block(blks[0]) if blks else {}

        for name in names:
            nm = re.match(r"^([a-z_]+?)(\d*)$", name.lower())
            base, idx = (nm.group(1), nm.group(2)) if nm else (name.lower(), "")
            whole = RACK_PREFIX.match(name.lower())
            if name.lower() in vocab.fx or (whole and whole.group(2) in vocab.fx):
                base, idx = name.lower(), ""
            base = SECTION_ALIASES.get(base, base)
            n = int(idx) if idx else 0

            if is_off:
                P.off.add((base, n))
                # An effect that is off still OCCUPIES ITS RACK SLOT.
                # destModuleID on an FX is its position in the rack, so
                # dropping the entry renumbers every effect after it and
                # silently retargets every modulation pointing at them. The
                # idc grammar holds the slot with `off _` for exactly this
                # reason; the restorer has to honour it.
                if base in vocab.fx or RACK_PREFIX.match(base):
                    _section(P, vocab, base, n, {}, [], rest, off=True)
                continue

            handled = _section(P, vocab, base, n, head, blks, rest)
            if handled == "wavetable":
                last_wt = max(n - 1, 0)
            elif handled is False:
                # unknown section: try to fuzzy match it to a known one
                alt = closest(base, {k: k for k in KNOWN_SECTIONS}, "section")
                if alt and _section(P, vocab, alt, n, head, blks, rest):
                    LOG.log("fix", f"section {base!r} read as {alt!r}")
                else:
                    LOG.log("drop", f"section {name!r}")
    return P


KNOWN_SECTIONS = {"voice", "osc", "wavetable", "noise", "sub", "filter",
                  "routing", "env", "lfo", "effect_chain"}


def _section(P, vocab, base, n, head, blks, rest, off=False):
    """One section line. Returns False if the name means nothing here."""

    def apply(mod_key, family, block, skip=()):
        for k, v in block.items():
            if k in skip or k == "_" or k.startswith("_"):
                continue
            kp, fam = vocab.kparam(family, k)
            if kp is None:
                continue
            owner = vocab.split_family(mod_key, kp, fam)
            child = {"Oscillator3": ("NoiseOsc", f"NoiseOsc{n if n else 3}"),
                     "Oscillator4": ("SubOsc", "SubOsc4")}.get(mod_key)
            if child and owner == child[0]:
                P.children[mod_key].setdefault(child[1], {})[kp] = \
                    vocab.value(owner, kp, v)
                if owner != fam:
                    LOG.log("fix", f"{k} routed to {owner}, not {mod_key}")
            else:
                P.setp(mod_key, kp, vocab.value(fam, kp, v))

    if base == "voice":
        apply("Global0", "Global", head)
    elif base == "osc" and 1 <= n <= 3:
        apply(f"Oscillator{n-1}", "Oscillator", head)
    elif base == "wavetable" and 1 <= n <= 3:
        P.children[f"Oscillator{n-1}"]["_wt"] = head
        P.engines[n - 1] = head.get("engine", "wt")
        return "wavetable"
    elif base == "noise":
        apply("Oscillator3", "Oscillator", head, skip=("name",))
        if "name" in head:
            P.children["Oscillator3"]["_path"] = head["name"]
    elif base == "sub":
        apply("Oscillator4", "Oscillator", head)
    elif base == "filter" and n in (1, 2):
        apply(f"VoiceFilter{n-1}", "VoiceFilter", head)
    elif base in vocab.fx or RACK_PREFIX.match(base):
        rack, fxname = 0, base
        rm = RACK_PREFIX.match(base)
        if rm:
            rack = max(int(rm.group(1)) - 1, 0)
            fxname = rm.group(2)
        cls, tid = vocab.fx.get(fxname, (None, None))
        if not cls:
            return False
        fam = re.match(r"^([A-Za-z]+?)\d*$", cls).group(1)
        pp, ir = {}, None
        for k, v in head.items():
            if k == "_":
                continue
            if k == "ir" and cls == "FXConv":
                ir = vocab.ir_path(str(v))
                continue
            kp, f2 = vocab.kparam(fam, k)
            if kp:
                pp[kp] = vocab.value(f2, kp, v)
        if off:
            pp["kParamEnable"] = 0.0
        P.fx.append({"name": fxname, "cls": cls, "type": tid,
                     "pp": pp, "rack": rack, "off": off, "ir": ir})
    elif base == "routing":
        pos = 0
        for k, v in head.items():
            if "." in k:
                lab, _, pk = k.partition(".")
                kp = vocab.param.get("RoutingSlot", {}).get(pk)
                if lab in ROUTING_SLOTS and kp:
                    P.setp(f"RoutingSlot{ROUTING_SLOTS.index(lab)}", kp, try_float(v, 0.0))
                else:
                    LOG.log("drop", f"routing {k}")
                continue
            slot = ROUTING_SLOTS.index(k) if k in ROUTING_SLOTS else pos
            pos += 1
            if slot < len(ROUTING_SLOTS):
                P.routing[slot] = v
    elif base == "env" and 1 <= n <= 4:
        apply(f"Env{n-1}", "Env", head)
    elif base == "lfo" and 1 <= n <= 10:
        key = f"LFO{n-1}"
        apply(key, "LFO", head)
        if len(blks) > 1:
            sh = parse_block(blks[1])
            pts = sh.get("points")
            if pts is None and sh.get("shape") not in (None, "custom", True):
                pts = vocab.shape(sh["shape"])
            if pts:
                c = curve_object(pts, sh.get("loop"), vocab)
                if c:
                    P.curves[key] = c
    else:
        return False
    return True


# =============================================================================
# PHASE 3 - MODULATION MAP
# =============================================================================

class ModMap:
    """Which destinations are reachable, and by what.

    Vital needs a graph here because a slot can modulate another slot's amount.
    Serum does not: measured across 52,229 slots, zero target a ModSlot. So one
    level is the whole story, and pretending otherwise would be inventing
    structure the format does not have."""

    def __init__(self, rows):
        self.by_dest = defaultdict(list)
        for r in rows:
            if r.get("dest_key"):
                self.by_dest[r["dest_key"]].append(r)

    def modulated(self, dest_key):
        """True if something can actually deliver a non-zero amount here."""
        for r in self.by_dest.get(dest_key, ()):
            if r["src"] in USER_SOURCES:
                return True                    # the player supplies the amount
            if r["amount"]:
                return True
        return False


def dest_key(ts, mid, pn):
    return f"{ts}/{mid}/{pn}"


# =============================================================================
# PHASES 4-7 - REPAIR
# =============================================================================

# Which parameters actually carry an effect's audibility. This is per-effect
# plugin knowledge and it does NOT generalise, which is the whole reason the og
# writes these out by hand instead of applying one rule to every effect.
#
# FXHyperD is why. It is two effects in one, Hyper and Dimension, and it has
# TWO wet controls. A generic "kParamWet == 0 means dead" rule disabled a
# preset that was audible entirely through kParamDimEWet at 72.37.
# Paid for 2026-08-19 on BA Marble Power.
AUDIBILITY = {
    "FXHyperD":     ["kParamWet", "kParamDimEWet"],
    "FXChorus":     ["kParamWet"],
    "FXDelay":      ["kParamWet"],
    "FXDistortion": ["kParamWet"],
    "FXFilter":     ["kParamWet"],
    "FXFlanger":    ["kParamWet"],
    "FXPhaser":     ["kParamWet"],
    "FXReverb":     ["kParamWet"],
    "FXComp":       ["kParamWet"],
    "FXEQ":         [],
    "FXBode":       ["kParamWet"],
    "FXConv":       ["kParamWet"],
    "FXUtils":      ["kParamWet"],
    "FXSplit":      [],
    "FXSplit3":     [],
    "FXSplitMS":    [],
}


def repair(P, mm, vocab, mutate=True):
    """Normalisation, and it is for the corpus, not for the file.

    An effect written `on` carrying a full parameter block that produces no
    sound teaches the model that `on` sometimes means nothing. At 9B on 6k
    examples that inconsistency is pure cost: the parameters underneath it
    carry zero signal and the model still has to spend capacity on them. So a
    dead component is normalised to dead, here and in the writer.

    What must be right is the audibility test itself, and that is per-effect
    plugin knowledge that does not generalise. See AUDIBILITY."""

    # -- phase 4: effects that cannot be heard -----------------------------
    # destModuleID on an FX is its position within its own rack, so the slot
    # index has to be counted per rack, not across the whole list.
    rack_pos = Counter()
    for e in P.fx:
        r = e.get("rack", 0)
        slot = rack_pos[r]
        rack_pos[r] += 1
        pp = e["pp"]
        if pp.get("kParamEnable") == 0.0:
            continue                                    # already off, honestly
        # The key is the same string serum2_to_idc.py logs for this decision,
        # so the two event logs join on it and a disagreement is a row present
        # in one and missing from the other.
        key = f"{e['cls']}/{slot}"
        controls = AUDIBILITY.get(e["cls"])
        if controls is None:
            LOG.log("warn", f"{e['name']}: no audibility knowledge, left alone")
            LOG.event("audible", key, f"no knowledge for {e['cls']}")
            continue
        if not controls:
            LOG.event("audible", key, "no wet control on this class")
            continue
        vals, mods, live = [], [], False
        for c in controls:
            v = pp.get(c)
            m = mm.modulated(dest_key(e["cls"], slot, c))
            vals.append(f"{c}={'absent' if v is None else v}")
            mods.append(f"{c}:mod={int(m)}")
            if v is None or v != 0.0 or m:
                live = True
                break
        detail = f"{' '.join(vals)} | {' '.join(mods)}"
        if live:
            LOG.event("audible", key, detail)
            continue
        LOG.event("inaudible", key, detail)
        LOG.log("warn", f"{e['name']}: all of {controls} at 0 and unmodulated")
        if mutate:
            pp["kParamEnable"] = 0.0
            LOG.log("fix", f"{e['name']} disabled")

    # -- phase 5: routing into a filter that is off ------------------------
    f1 = P.modules.get("VoiceFilter0", {}).get("kParamEnable", 1.0) != 0.0
    f2 = P.modules.get("VoiceFilter1", {}).get("kParamEnable", 1.0) != 0.0
    if not f1 and not f2:
        for slot, v in list(P.routing.items()):
            if str(v).startswith("filter"):
                LOG.log("warn", f"routing slot {slot} -> filter, both voice "
                                f"filters read as off")
                if mutate:
                    P.routing[slot] = "master"
                    LOG.log("fix", f"routing slot {slot}: filter -> master")

    # -- phase 6: sources at zero with nothing to lift them -----------------
    for oi in range(5):
        vol = P.modules.get(f"Oscillator{oi}", {}).get("kParamVolume")
        if vol != 0.0:
            continue
        if mm.modulated(dest_key("Oscillator", oi, "kParamVolume")):
            continue
        LOG.log("warn", f"osc{oi+1} volume 0 with no modulation on it")

    # -- phase 7: structural, always applies -------------------------------
    kept = [r for r in P.mods if r.get("resolved")]
    lost = len(P.mods) - len(kept)
    if lost:
        LOG.log("drop", f"{lost} modulation row(s) with an unresolvable target")
    if len(kept) > 64:
        LOG.log("drop", f"{len(kept) - 64} modulation row(s) past the 64 slots")
        kept = kept[:64]
    P.mods = kept


# =============================================================================
# PHASE 2 (late) - RESOLVE MODULATION ROWS
# =============================================================================

TAG_TO_KPARAM = {
    "bipolar": "kParamBipolar", "bypass": "kParamBypass",
    "curve_in": "kParamCurveIn", "aux_inverted": "kParamAuxInverted",
    "aux_curve": "kParamAuxCurve", "aux_curve_data": "kParamAuxCurveData",
    "out": "kParamOut",
}


def resolve_mods(P, vocab):
    # label -> (rack, slot), from the effect lines actually parsed. Off
    # effects are in P.fx too, which is why they must not be dropped: their
    # slot is what every later index counts from.
    fxmap, pos = {}, Counter()
    for e in P.fx:
        rk = e.get("rack", 0)
        label = e["name"] if rk == 0 else f"r{rk+1}_{e['name']}"
        fxmap[label] = (rk, pos[rk])
        pos[rk] += 1
    P.fxmap = fxmap
    for r in P.mods:
        blk = r["blk"]
        dest = next((k for k in blk if k not in TAG_TO_KPARAM and k != "_"), None)
        if dest is None:
            LOG.log("drop", "mod row with no destination")
            continue
        res = vocab.resolve_dest(dest, P.fxmap)
        if res is None:
            continue
        ts, mid, pn, pid = res
        amount = try_float(blk.get(dest), 0.0) or 0.0
        r.update({
            "resolved": True,
            "ts": ts, "mid": mid, "pn": pn, "pid": pid,
            "dest_key": dest_key(ts, mid, pn),
            "amount": amount,
            "src": vocab.source_id(r["srcs"][0] if r["srcs"] else "off"),
            "aux": vocab.source_id(r["srcs"][1]) if len(r["srcs"]) > 1 else 0,
            "tags": {TAG_TO_KPARAM[k]: try_float(v, 0.0)
                     for k, v in blk.items() if k in TAG_TO_KPARAM},
        })


# =============================================================================
# PHASE 8 - WRITEOUT
# =============================================================================

def scalar_curve(blk):
    obj = curve_object(blk.get("points") or SCALAR_IDENTITY) or curve_object(SCALAR_IDENTITY)
    obj["legato"] = str(blk.get("legato", "1")) not in ("0", "false", "False")
    return obj


SCALAR_IDENTITY = "0,1|0.5 1,0|0.5"


STEPPED = {("FXHyperD", "kParamUnison"): 7}


def stepped(cls, pp):
    if not isinstance(pp, dict):
        return
    for (c, k), span in STEPPED.items():
        v = pp.get(k)
        if c == cls and isinstance(v, (int, float)) and 0 < v < span and v == round(v):
            pp[k] = struct.unpack("f", struct.pack("f", v / span))[0] * span  # float32(norm)*span, the form Serum saves; an exact int loads as default


def writeout(P, init, vocab):
    """Merge onto the scaffold. The scaffold is never rebuilt, only written
    onto, so anything this preset does not mention keeps Serum's own default."""
    data = copy.deepcopy(init["data"])
    meta = copy.deepcopy(init["metadata"])

    def plain(mod_key):
        m = data.setdefault(mod_key, {})
        pp = m.get("plainParams")
        if not isinstance(pp, dict):
            pp = {}
            m["plainParams"] = pp
        return pp

    # metadata
    for k in ("presetName", "presetAuthor", "presetDescription", "tags"):
        if k in P.meta:
            meta[k] = data[k] = P.meta[k]

    # module parameters
    for mod_key, params in P.modules.items():
        if not params:
            continue
        plain(mod_key).update(params)

    # mode children and payloads
    for mod_key, kids in P.children.items():
        oi = int(mod_key[-1]) if mod_key[-1].isdigit() else 0
        for ck, cv in kids.items():
            if ck in ("_wt", "_flex", "_path"):
                continue
            child = data.setdefault(mod_key, {}).setdefault(ck, {})
            pp = child.get("plainParams")
            if not isinstance(pp, dict):
                pp = {}
                child["plainParams"] = pp
            pp.update(cv)
        if "_path" in kids and mod_key == "Oscillator3":
            child = data[mod_key].setdefault("NoiseOsc3", {})
            child["relativePathToNoiseSample"] = vocab.wavetable_path(
                kids["_path"], noise=True)
        if "_wt" in kids:
            write_engine(data, mod_key, oi, kids["_wt"], kids.get("_flex"),
                         P.engines.get(oi, "wt"), vocab)

    # LFO curves
    for key, curve in P.curves.items():
        data.setdefault(key, {})["curveData"] = curve

    # routing
    for slot, v in P.routing.items():
        if v in (True, "default", "_"):
            continue
        dest = closest(str(v), ROUTING_DEST, "routing dest")
        if dest:
            plain(f"RoutingSlot{slot}")["kParamRoutingDest"] = dest

    # macros
    for n, v in P.macros.items():
        if 1 <= n <= 8:
            plain(f"Macro{n-1}")["kParamValue"] = v

    data["scalars"] = {k: scalar_curve(P.scalars.get(k, {})) for k in ("velo", "note")}

    fx = P.fx
    if P.fx_order is not None:
        pool = defaultdict(list)
        for e in P.fx:
            pool[e["name"] if not e.get("rack") else f"r{e['rack'] + 1}_{e['name']}"].append(e)
        fx = [pool[n].pop(0) for n in P.fx_order if pool[n]]
        LOG.event("chain", "rack", f"{len(fx)} of {len(P.fx)} effect lines in the chain")
    racks = defaultdict(list)
    for e in fx:
        stepped(e["cls"], e["pp"])
        entry = {e["cls"]: {"plainParams": e["pp"] or "default"}}
        if e.get("ir"):
            entry[e["cls"]]["relativePathToIR"] = e["ir"]
        if e["type"] is not None:
            entry["type"] = e["type"]
        racks[e.get("rack", 0)].append(entry)
    for r, entries in racks.items():
        if r > 2:
            LOG.log("drop", f"FX rack {r} does not exist, {len(entries)} effect(s)")
            continue
        data[f"FXRack{r}"] = {"FX": entries, "displayName": "",
                              "plainParams": "default"}

    # ---- structural guard, and it is the one that stops a DAW crash --------
    # destModuleID on an FX is an index INTO the rack array. Serum indexes
    # straight into it, so a row pointing past the end is an out-of-bounds
    # read, and that is a full host crash rather than a wrong sound.
    #
    # A model will emit exactly this: a modulation aimed at an effect it did
    # not also put in the chain. So the index is validated against the
    # structure actually written, here, at the last possible moment. Nothing
    # earlier in the pipeline is trusted to have got it right.
    #
    # Paid for 2026-08-19 on BA CrowdScream Bass [ASL], which crashed the DAW.
    rack_len = {r: len((data.get(f"FXRack{r}") or {}).get("FX") or [])
                for r in range(3)}
    safe = []
    for r in P.mods:
        ts = r["ts"]
        if str(ts).startswith("FX") and not str(ts).startswith("FXRack"):
            mid = r["mid"]
            rack, slot = divmod(mid, FX_RACK_STRIDE)
            if rack > 2:
                LOG.log("drop", f"mod -> {ts}[{mid}] names rack {rack}, "
                                f"only 0..2 exist")
                LOG.event("guard", f"{ts}/{mid}", f"dropped, rack {rack}")
                continue
            if slot >= rack_len.get(rack, 0):
                LOG.log("drop", f"mod -> {ts}[{mid}] is rack {rack} slot "
                                f"{slot} but that rack holds "
                                f"{rack_len.get(rack,0)}; would read out of bounds")
                LOG.event("guard", f"{ts}/{mid}", "dropped, past rack end")
                continue
        safe.append(r)
    P.mods = safe

    # modulation matrix, compacted into ModSlot0..N-1
    for n in range(64):
        data[f"ModSlot{n}"] = {"plainParams": "default"}
    for n, r in enumerate(P.mods[:64]):
        pp = {"kParamAmount": r["amount"]}
        pp.update(r["tags"])
        slot = {"destModuleTypeString": r["ts"], "destModuleID": r["mid"],
                "destModuleParamName": r["pn"],
                "source": [r["src"], r["aux"]], "plainParams": pp}
        if r["pid"] is not None:
            slot["destModuleParamID"] = r["pid"]
        data[f"ModSlot{n}"] = slot
    if len(P.mods) > 64:
        LOG.log("warn", f"{len(P.mods)} modulation rows, only 64 slots exist")

    return {"metadata": meta, "data": data}


def write_engine(data, mod_key, oi, blk, flex, engine, vocab):
    engine = engine if engine in ENGINE_CHILD else "wt"
    child_name = f"{ENGINE_CHILD[engine]}{oi}"
    kind = OSC_TYPE_OUT.get(engine)
    if kind:
        data[mod_key].setdefault("plainParams", {})
        if not isinstance(data[mod_key]["plainParams"], dict):
            data[mod_key]["plainParams"] = {}
        data[mod_key]["plainParams"]["kParamType"] = kind
    child = data[mod_key].setdefault(child_name, {})

    name = blk.get("name")
    src = blk.get("src")
    if name and src == "embedded":
        arr = vocab.blob(name) if INLINE_BLOBS else None
        for stale in ("relativePathToWT", "numFrames", "sampleRate",
                      "numChannels", "tableDisplayName"):
            child.pop(stale, None)
        child["embeddedWTData"] = arr if arr is not None else name
        if arr is None and INLINE_BLOBS:
            LOG.log("warn", f"embedded table {name!r} not inlined; Serum will "
                            f"name a table it does not carry")
        elif arr is not None and len(arr) % 2048:
            LOG.log("warn", f"embedded table {name!r} is {len(arr)} samples, "
                            f"not a multiple of 2048")
    elif name:
        for stale in ("numFrames", "sampleRate", "numChannels", "tableDisplayName"):
            child.pop(stale, None)
        child["relativePathToWT"] = vocab.wavetable_path(name)
    fr = try_float(blk.get("frames"))
    if fr:
        child["numFrames"] = int(round(fr * 2048))
    if blk.get("display_name"):
        child["tableDisplayName"] = str(blk["display_name"]).replace("_", " ")
    iv = try_float(blk.get("interp_after_load"))
    if iv is not None:
        child["interpolateAfterLoad"] = int(iv)
    if flex:
        c = curve_object(flex)
        if c:
            child["flex"] = c

    fam = re.match(r"^([A-Za-z]+?)\d*$", ENGINE_CHILD[engine]).group(1)
    for k, v in blk.items():
        if k in ("engine", "src", "name", "frames", "display_name",
                 "interp_after_load", "_") or k.startswith("_"):
            continue
        kp, f2 = vocab.kparam(fam, k)
        if not kp:
            continue
        pp = child.get("plainParams")
        if not isinstance(pp, dict):
            pp = {}
            child["plainParams"] = pp
        pp[kp] = vocab.value(f2, kp, v)


# =============================================================================
# CONTAINER
# =============================================================================

def _cbor(o, out):
    """CBOR, encoding a float as float32 whenever that is lossless.

    THIS IS NOT A SIZE OPTIMISATION. Serum writes float32 for values that
    survive it and float64 for the rest, measured on its own output: 442 and
    39 in one preset. cbor2.dumps emits float64 for everything, so every
    preset this project ever packed was byte-shaped differently from anything
    Serum produces, while decoding to identical data and therefore passing
    every check here.

    Measured 2026-08-19: with this encoder, unpack followed by pack of a
    Serum-written preset is BYTE IDENTICAL, 15571 bytes. With cbor2.dumps it
    was 17323. cbor2's canonical=True is not the answer either, it picks
    float16 where it can and Serum never does.
    """
    def head(major, n):
        if n < 24:
            out.append(bytes([major << 5 | n]))
        elif n < 256:
            out.append(bytes([major << 5 | 24, n]))
        elif n < 65536:
            out.append(bytes([major << 5 | 25]) + struct.pack(">H", n))
        elif n < 2 ** 32:
            out.append(bytes([major << 5 | 26]) + struct.pack(">I", n))
        else:
            out.append(bytes([major << 5 | 27]) + struct.pack(">Q", n))

    if o is None:
        out.append(b"\xf6")
    elif o is True:
        out.append(b"\xf5")
    elif o is False:
        out.append(b"\xf4")
    elif isinstance(o, int):
        head(0, o) if o >= 0 else head(1, -o - 1)
    elif isinstance(o, float):
        f = struct.pack(">f", o)
        if struct.unpack(">f", f)[0] == o or o != o:
            out.append(b"\xfa" + f)
        else:
            out.append(b"\xfb" + struct.pack(">d", o))
    elif isinstance(o, str):
        b = o.encode("utf-8")
        head(3, len(b))
        out.append(b)
    elif isinstance(o, bytes):
        head(2, len(o))
        out.append(o)
    elif isinstance(o, list):
        head(4, len(o))
        for x in o:
            _cbor(x, out)
    elif isinstance(o, dict):
        head(5, len(o))
        for k, v in o.items():
            _cbor(k, out)
            _cbor(v, out)
    else:
        raise TypeError("cbor: %s" % type(o))


_BLOB_IDS = {"set": None}


def blob_ids():
    """Every id the dedup map defines, from serum2/blob_index.json.

    2786 of them, over four prefixes: WAVE_DATA, SAMPLES, NOISE_DATA and
    TUNING_DATA. Matched against this set rather than against a name pattern,
    because a pattern also matches real wavetable names like BASS_06_saw_lead
    and a false positive here would corrupt a working preset.
    """
    if _BLOB_IDS["set"] is None:
        p = pathlib.Path(CACHE) / "blob_index.json"
        try:
            _BLOB_IDS["set"] = set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            _BLOB_IDS["set"] = set()
    return _BLOB_IDS["set"]


def find_blob_ids(node, path="", out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            find_blob_ids(v, path + "/" + str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find_blob_ids(v, path + "[%d]" % i, out)
    elif isinstance(node, str) and node in blob_ids():
        out.append((path, node))
    return out


# The four prefixes blob_index.json uses. Matching on these rather than on
# membership is the whole point of find_blob_shaped below.
BLOB_PREFIXES = ("WAVE_DATA_", "SAMPLES_", "NOISE_DATA_", "TUNING_DATA_")


def find_blob_shaped(node, path="", out=None):
    """Every string that LOOKS like a dedup id, known or not.

    find_blob_ids answers 'is this an id the corpus defines'. That question is
    useless as a safety check, because an id the corpus defines is an id
    inline_blobs already replaced. The id that reaches pack() unresolved is
    the one the MODEL INVENTED, which is by construction absent from
    blob_index.json and therefore invisible to a membership test.

    Measured 2026-08-23 on presets_kbog6: three of 26 restored presets took
    the DAW down, each carrying exactly one invented id -- WAVE_DATA_hollow_ibex,
    WAVE_DATA_trace_rust, WAVE_DATA_pour_clay -- as a literal string in
    LFO0/curveData where Serum indexes floats. inline_blobs did not collect
    them and pack()'s guard did not refuse them, both for the same reason.

    The prefix is safe where a bare pattern is not: blob_ids()'s docstring
    warns that a pattern also matches real wavetable names like
    BASS_06_saw_lead, and none of those carry one of these four prefixes.
    """
    out = [] if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            find_blob_shaped(v, path + "/" + str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            find_blob_shaped(v, path + "[%d]" % i, out)
    elif isinstance(node, str) and node.startswith(BLOB_PREFIXES):
        out.append((path, node))
    return out


_BLOB_LEN = {"map": None, "by_len": None, "by_pref": None}


def blob_lengths():
    """id -> element count for every blob in the reference map.

    Cached in blob_lengths.json: the map itself is 95 MB and this needs 2786
    integers out of it, on a path that runs per preset.
    """
    if _BLOB_LEN["map"] is None:
        cache = pathlib.Path(CACHE) / "blob_lengths.json"
        try:
            m = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            m = {}
            src = pathlib.Path(BLOB_MAP)
            if src.exists():
                with open(src, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        r = json.loads(line)
                        b = r.get("blob")
                        m[r["id"]] = len(b) if hasattr(b, "__len__") else -1
                try:
                    cache.write_text(json.dumps(m, separators=(",", ":")),
                                     encoding="utf-8")
                except Exception:
                    pass
        _BLOB_LEN["map"] = m
        by_len, by_pref = defaultdict(list), defaultdict(list)
        for k, L in m.items():
            pref = k.split("_")[0]
            by_len[(pref, L)].append(k)
            by_pref[pref].append(k)
        _BLOB_LEN["by_len"], _BLOB_LEN["by_pref"] = by_len, by_pref
    return _BLOB_LEN["map"], _BLOB_LEN["by_len"], _BLOB_LEN["by_pref"]


def _nearest(bid, pool):
    """Nearest id in pool by edit distance -> (id, distance)."""
    if not pool:
        return None, None
    if bid in pool:
        return bid, 0
    best = min(pool, key=lambda k: levenshtein(bid, k))
    return best, levenshtein(bid, best)


def resolve_curve_ids(doc):
    """Snap each LFO curve's three blob ids onto one real, coherent triple.

    This is the same repair closest() already performs for params, enums, mod
    destinations, wavetable paths and LFO shapes -- the identifiers are
    word-shaped precisely so edit distance means something. It was never
    wired up for curve blobs, which made a curve the one vocabulary where a
    near miss was fatal instead of corrected.

    Measured 2026-08-23 over 86 generated presets: 26 of 138 WAVE_DATA ids
    (18.8%) named nothing that exists, affecting 25.6% of presets.

    What makes this more than a closest() call is that xVals, yVals and
    curveVals are ONE curve. All 43,881 curves in the raw corpus have three
    equal-length arrays, no exceptions, and a model picks the three ids
    independently -- so even a triple of entirely REAL ids routinely arrives
    as three unrelated lengths. Matching each to its own nearest neighbour
    would preserve that. So the length is chosen first, by majority and
    otherwise from the most confident match, and the remaining ids are then
    matched only against ids of that length. No length in the WAVE_DATA pool
    has fewer than two candidates, so the constrained pool is never empty.

    Returns the number of curves rewritten.
    """
    lens, by_len, by_pref = blob_lengths()
    if not lens:
        return 0
    data = doc.get("data")
    if not isinstance(data, dict):
        return 0
    ROLES = ("xVals", "yVals", "curveVals")
    fixed = 0
    for key, mod in data.items():
        if not isinstance(mod, dict):
            continue
        cd = mod.get("curveData")
        if not isinstance(cd, dict):
            continue
        ids = [cd.get(r) for r in ROLES]
        if not all(isinstance(i, str) and i.startswith(BLOB_PREFIXES)
                   for i in ids):
            continue
        pref = ids[0].split("_")[0]
        pool = by_pref.get(pref)
        if not pool:
            continue
        first = [_nearest(i, pool) for i in ids]
        if any(b is None for b, _ in first):
            continue
        cand = [lens[b] for b, _ in first]
        counts = Counter(cand)
        top, n = counts.most_common(1)[0]
        target = top if n >= 2 else cand[min(range(3),
                                             key=lambda i: first[i][1])]
        for role, bid, (b0, d0) in zip(ROLES, ids, first):
            if lens[b0] == target:
                b, d = b0, d0
            else:
                b1, d1 = _nearest(bid, by_len.get((pref, target), []))
                b, d = (b1, d1) if b1 else (b0, d0)
            cd[role] = b
            if b != bid:
                LOG.log("fallback", f"{key}/curveData/{role}: {bid!r} -> "
                                    f"{b!r} (d={d}, len={target})")
        fixed += 1
    return fixed


def repair_curves(doc):
    """Enforce the curve invariant, and drop any curve that cannot meet it.

    resolve_curve_ids runs first and should leave nothing here to drop. This
    stays as the last line of defence: a curve that still violates the
    invariant is data Serum indexes past the end of, which is a host crash
    rather than a wrong sound.

    Measured over all 43,881 LFO curves in the 5,948 raw corpus presets, with
    ZERO exceptions: xVals, yVals and curveVals are the same length, and
    numPoints is that length minus one. So this is not a heuristic.

    The three arrays are ONE object. The .idc spells them as three dedup ids
    (`points:@ID1 ID2 ID3`) because each array dedups separately across the
    corpus, but a model picks the three independently and gets a curve whose
    x, y and curve arrays came from three unrelated presets. That is not a
    repairable curve, it is three fragments, so it is removed and init.json's
    default stands -- rule 1 of this file. Leaving a partial one is exactly
    what crashed the DAW.

    Returns the number of curves dropped.
    """
    data = doc.get("data")
    if not isinstance(data, dict):
        return 0
    ROLES = ("xVals", "yVals", "curveVals")
    dropped = 0
    for key, mod in data.items():
        if not isinstance(mod, dict):
            continue
        cd = mod.get("curveData")
        if not isinstance(cd, dict):
            continue
        arrs = [cd.get(r) for r in ROLES]
        if all(a is None for a in arrs):
            # init.json's own empty-curve state for an LFO nobody touched.
            # Not a defect and not ours to remove: rule 1 says the scaffold
            # stands wherever the preset said nothing.
            continue
        if not all(isinstance(a, list) for a in arrs):
            bad = [r for r in ROLES if not isinstance(cd.get(r), list)]
            LOG.log("drop", f"{key}/curveData: {bad} not point data "
                            f"({cd.get(bad[0])!r}); curve reset to default")
            mod.pop("curveData", None)
            dropped += 1
            continue
        if len({len(a) for a in arrs}) != 1:
            LOG.log("drop", f"{key}/curveData: lengths "
                            f"{[len(a) for a in arrs]} differ; the three "
                            f"arrays are one curve, reset to default")
            mod.pop("curveData", None)
            dropped += 1
            continue
        # numPoints is never written by curve_object's blob branch and
        # writeout replaces the scaffold's curveData whole, so it is absent
        # on every blob-form curve until here.
        cd["numPoints"] = max(len(arrs[0]) - 1, 1)
    return dropped


def inline_blobs(doc, refmap=None):
    """Replace every dedup id in the document with the data it stands for.

    A preset that names a blob it does not carry CRASHES Serum. Measured
    2026-08-19: a restored preset carried WAVE_DATA_tide_bright and two others
    in LFO5/curveData, where Serum wants 17 floats and got a 21-character
    string, and it took the DAW down. Inlining those three, with nothing else
    changed, made the same preset load.

    PIPELINE 6 describes inlining as conditional on the .idc containing
    src:embedded. LFO curve blobs never take that route, so the guard was
    written for one path and the data arrived by another. Hence this is
    unconditional and works on the finished document, where any id at all is
    unambiguously wrong.
    """
    # find_blob_shaped, NOT find_blob_ids: an id the model invented is absent
    # from blob_index.json, so a membership scan walks straight past the only
    # kind that ever reaches pack() unresolved. It stays a string here, is
    # reported as missing, and repair_curves takes its curve down.
    # Snap near-miss and invented ids onto a real, length-coherent triple
    # BEFORE anything is looked up, so the refmap scan below sees only ids
    # that exist and repair_curves has nothing left to drop.
    resolve_curve_ids(doc)
    want = {i for _, i in find_blob_shaped(doc)}
    if not want:
        repair_curves(doc)
        return doc, 0, []
    refmap = refmap or BLOB_MAP
    blobs = {}
    with open(refmap, encoding="utf-8") as f:
        for line in f:
            if not any(w in line[:400] for w in want):
                continue
            r = json.loads(line)
            if r.get("id") in want:
                blobs[r["id"]] = r["blob"]
            if len(blobs) == len(want):
                break

    def sub(n):
        if isinstance(n, dict):
            return {k: sub(v) for k, v in n.items()}
        if isinstance(n, list):
            return [sub(v) for v in n]
        return blobs.get(n, n) if isinstance(n, str) else n

    doc = sub(doc)
    repair_curves(doc)
    return doc, len(blobs), sorted(want - set(blobs))


def pack(obj, dst):
    """XferJson: magic, then (len, tag) pairs. Tag 0 is the JSON metadata, tag
    2 is zstd-compressed CBOR.

    The CBOR is byte-identical to Serum's own for the same data, see _cbor.
    The FILE still is not, because zstd level 3 here need not match whatever
    Xfer compressed with, so compare decoded data rather than file hashes."""
    import zstandard as zstd
    # NO PRESET LEAVES HERE CARRYING A BLOB ID. A preset that names data it
    # does not contain crashes Serum, and nothing downstream can detect it:
    # the file is valid CBOR, the document is well formed, and every check in
    # this repo passes. So it is refused at the one place every preset goes
    # through, rather than remembered at each call site.
    stray = find_blob_shaped(obj["data"])
    if stray:
        raise ValueError(
            "refusing to pack: %d unresolved blob ids, first is %s at %s. "
            "Call inline_blobs() first." % (len(stray), stray[0][1], stray[0][0]))
    m = json.dumps(obj["metadata"], separators=(",", ":")).encode()
    _parts = []
    _cbor(obj["data"], _parts)
    c = b"".join(_parts)
    z = zstd.ZstdCompressor(level=3).compress(c)
    out = bytearray(MAGIC)
    out += struct.pack("<II", len(m), 0)
    out += m
    out += struct.pack("<II", len(c), 2)
    out += z
    pathlib.Path(dst).write_bytes(out)


def unpack(src, dst):
    import cbor2, zstandard as zstd
    buf = pathlib.Path(src).read_bytes()
    if not buf.startswith(MAGIC):
        raise ValueError("not a SerumPreset: missing XferJson magic")
    off = len(MAGIC)
    jlen, _ = struct.unpack_from("<II", buf, off); off += 8
    meta = json.loads(buf[off:off + jlen]); off += jlen
    clen, _ = struct.unpack_from("<II", buf, off); off += 8
    cbor = zstd.ZstdDecompressor().decompress(buf[off:])
    if len(cbor) != clen:
        LOG.log("warn", f"cbor length {len(cbor)} != declared {clen}")
    pathlib.Path(dst).write_text(
        json.dumps({"metadata": meta, "data": cbor2.loads(cbor)}, indent=2),
        encoding="utf-8")


def load_blobs(path):
    m = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                m[r["id"]] = r["blob"]
    return m


# =============================================================================
# DRIVER
# =============================================================================

_CTX = {}


def context(need_blobs=False):
    if "vocab" not in _CTX:
        _CTX["init"] = json.load(open(os.path.join(HERE, "init.json"),
                                      encoding="utf-8"))
        sp = os.path.join(CACHE, "shapes.json")
        shapes = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
        _CTX["shapes"] = shapes
        _CTX["vocab"] = Vocab(os.path.join(HERE, "idc_reverse_map.json"), shapes)
        v = _CTX["vocab"]
        print(f"  scaffold {len(_CTX['init']['data'])} modules · "
              f"{len(shapes)} shapes · "
              f"{sum(len(x) for x in v.param.values())} params · "
              f"{len(v.wtpath)} wt paths")
    if need_blobs and not _CTX["vocab"].blobs:
        bp = BLOB_MAP
        if os.path.exists(bp):
            print("  inlining embedded tables, loading reference map ...",
                  end=" ", flush=True)
            b = load_blobs(bp)
            _CTX["vocab"].blobs = b
            _CTX["vocab"].blob_index = defaultdict(list)
            for k in b:
                _CTX["vocab"].blob_index[k.split("_")[0]].append(k)
            print(f"{len(b)} blobs")
        else:
            LOG.log("warn", "reference_map_serum.jsonl missing, tables not inlined")
    return _CTX["init"], _CTX["vocab"]


def restore_one(lines, init, vocab, index=0):
    P = parse_preset(lines, vocab, index)
    LOG.idx = index + 1
    resolve_mods(P, vocab)
    mm = ModMap([r for r in P.mods if r.get("resolved")])
    repair(P, mm, vocab)
    obj = writeout(P, init, vocab)
    LOG.end()
    return obj


def safe_name(s, fallback):
    s = re.sub(r'[\\/:*?"<>|]', "", s or "")
    s = re.sub(r"\s+", "_", s).strip("._")
    return s or fallback


def restore_file(path, outdir=None, limit=0):
    text = open(path, encoding="utf-8").read()
    init, vocab = context(need_blobs=INLINE_BLOBS and "src:embedded" in text)
    blocks = split_presets(text)
    root = (os.path.dirname(os.path.abspath(path)) if OUTPUT_NEXT_TO_INPUT
            else HERE)
    outdir = outdir or os.path.join(root, pathlib.Path(path).stem + "_restored")
    os.makedirs(outdir, exist_ok=True)
    print(f"  {len(blocks)} preset(s) -> {outdir}")

    seen, ok, failed, packfail = Counter(), 0, 0, 0
    for n, blk in enumerate(blocks):
        if limit and ok >= limit:
            break
        try:
            obj = restore_one(blk, init, vocab, n)
        except Exception as e:
            failed += 1
            LOG.log("drop", f"preset {n+1}: {type(e).__name__}: {e}")
            continue
        nm = safe_name(obj["metadata"].get("presetName"), f"preset_{n+1}")
        seen[nm] += 1
        if seen[nm] > 1:
            nm = f"{nm}_{seen[nm]-1}"
        with open(os.path.join(outdir, nm + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
        if WRITE_SERUMPRESET:
            try:
                # Inline before packing, always. A dedup id left in the
                # document is a preset that names data it does not carry, and
                # Serum crashes on it: measured on LFO curveData, where it
                # wants 17 floats and finds a 21-character string. pack()
                # refuses outright, so without this every affected preset
                # would quietly produce no file at all.
                obj, _n, missing = inline_blobs(obj)
                if missing:
                    LOG.log("warn", f"blob {nm}: {len(missing)} ids not in the "
                                     f"reference map, first {missing[0]}")
                pack(obj, os.path.join(outdir, nm + ".SerumPreset"))
            except Exception as e:
                packfail += 1
                LOG.log("warn", f"pack {nm}: {type(e).__name__}: {e}")
        ok += 1
    # packfail is reported, not buried in the log. A preset that restores and
    # then fails to pack produced no loadable file, which is the same outcome
    # as failing outright, and the old summary counted it as restored.
    print(f"  restored {ok}" + (f", {failed} failed" if failed else "")
          + (f", {packfail} RESTORED BUT DID NOT PACK" if packfail else "")
          + (" (+ .SerumPreset)" if WRITE_SERUMPRESET else ""))
    return ok


EXTS = {".idc", ".serumpreset", ".json"}


def gather(p):
    path = pathlib.Path(str(p).strip().strip('"').strip("'"))
    if path.is_file():
        return [path]
    if not path.is_dir():
        print(f"  not found: {p}")
        return []
    files = [f for f in sorted(path.iterdir())
             if f.is_file() and f.suffix.lower() in EXTS]
    idc = [f for f in files if f.suffix.lower() == ".idc"]
    return idc or files


def process(path, limit=0, outdir=None):
    ext = path.suffix.lower()
    print(f"\n{path.name}")
    if ext == ".idc":
        restore_file(str(path), outdir, limit)
    elif ext == ".serumpreset":
        dst = path.with_suffix(".json")
        unpack(path, dst)
        print(f"  unpacked -> {dst.name}")
    elif ext == ".json":
        dst = path.with_suffix(".SerumPreset")
        doc, n, missing = inline_blobs(json.loads(path.read_text(encoding="utf-8")))
        if n:
            print(f"  inlined {n} blob(s)")
        if missing:
            print(f"  WARNING {len(missing)} blob ids not in the reference map: "
                  f"{missing[:3]}")
        pack(doc, dst)
        print(f"  packed -> {dst.name}")
    else:
        print(f"  skipped, unrecognised extension {path.suffix}")


BANNER = """
restore_serum2 -- .idc into a loadable Serum 2 preset

  .idc          -> .json + .SerumPreset    (init.json as scaffold)
  .SerumPreset  -> .json                   (container only)
  .json         -> .SerumPreset            (container only)

a folder does every recognised file in it, one level deep.
"""


def run(paths, limit=0, outdir=None, verbose=False):
    for p in paths:
        for f in gather(p):
            try:
                process(f, limit, outdir)
            except Exception as e:
                import traceback
                print(f"  FAILED: {type(e).__name__}: {e}")
                if verbose:
                    traceback.print_exc()
    print(f"\nlog: {LOG.summary()}")
    n_ev = LOG.write_events(EVENT_LOG_PATH)
    if n_ev:
        print(f"events: {EVENT_LOG_PATH} ({n_ev} rows)")
    if verbose:
        print(LOG.report())
    else:
        for k, n in LOG.detail.most_common(10):
            print(f"   {n:>6}  {k}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="files or folders; omit to be asked")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-pack", action="store_true")
    ap.add_argument("--no-blobs", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log", action="store_true",
                    help=f"write {os.path.basename(EVENT_LOG_PATH)} "
                         f"(same as setting EVENT_LOG = True in this file)")
    a = ap.parse_args()

    global WRITE_SERUMPRESET, INLINE_BLOBS, EVENT_LOG
    if a.no_pack:
        WRITE_SERUMPRESET = False
    if a.no_blobs:
        INLINE_BLOBS = False
    if a.log:
        EVENT_LOG = True

    if a.paths:
        run(a.paths, a.limit, a.outdir, a.verbose)
        return 0

    print(BANNER)
    while True:
        try:
            raw = input("file or folder (q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if raw.lower() in ("q", "quit", "exit"):
            break
        if not raw:
            continue
        LOG.reset()
        run([raw], a.limit, a.outdir, a.verbose)
        print()
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    if not sys.argv[1:] and sys.stdin.isatty():
        try:
            input("\nPress Enter to exit...")
        except (EOFError, KeyboardInterrupt):
            pass
    sys.exit(rc)
