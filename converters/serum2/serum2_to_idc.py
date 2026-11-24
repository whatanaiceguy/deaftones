#!/usr/bin/env python3
"""
serum2_to_idc.py
----------------
serum2_presets.jsonl  ->  serum2_presets.idc   (all presets, '---' separated)

Same shape as vitals_to_jsonl_to_idc_v3.py:
  pass 1  convert every preset to .idc lines in memory
  pass 2  build the LFO shape refmap, frequency ranked
  pass 3  apply it
  write once

What is different from the Vital path, and why:
  * Serum's own format carries "plainParams": "default" as an init sentinel, so
    the diff against init is precomputed by the file. init.json does NOT hold
    resolved numeric defaults, it holds the same sentinel, so there is no
    defaults table to load and nothing to merge on the way out.
  * Wavetables are already named: either relativePathToWT (a factory path) or
    embeddedWTData (a word-pair id from reference_map_serum.jsonl). No
    wavetable refmap is minted here.
  * Every kParam* is auto-snake_cased, so an unmapped parameter still reaches
    the .idc rather than being dropped. OVERRIDE only renames.

Usage:
    python serum2_to_idc.py [in.jsonl] [--out FILE] [--limit N]
"""

import json, re, sys, os, argparse, hashlib
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- formatting

# keys whose value dies under 3 decimals. volume 0.00017 is the difference
# between silent and audible, and it rounds to 0.
# Significant figures, not decimal places. Absolute rounding cannot serve both
# osc volume 0.00017313 (which rounds to silence at 3dp) and a compressor
# release of 341.77752 ms (where 5dp is noise). One knob, and it is a knob.
SIGFIGS = 5
CURVE_SIGFIGS = 4

# ---- event log -------------------------------------------------------------
# Off by default. On, every decision this writer makes is appended to
# convert_events.log as one tab-separated row:
#
#   <preset_index>\t<preset_name>\t<kind>\t<key>\t<detail>
#
# `key` is deliberately the same string the restorer uses for the same
# decision, so the two logs join on (preset, key) and a disagreement between
# the two sides shows up as a row present in one and not the other. That is
# what it is for: the writer normalises 174 effects to off, the restorer then
# disables a few more, and the two should agree exactly.
EVENT_LOG = False
EVENT_LOG_PATH = os.path.join(HERE, "convert_events.log")
# ----------------------------------------------------------------------------


def fmt(v, key=None, sig=None):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        s = f"{v:.{sig or SIGFIGS}g}"
        if "e" in s or "E" in s:
            s = f"{v:.{(sig or SIGFIGS) + 4}f}".rstrip("0").rstrip(".")
        return s or "0"
    s = str(v)
    return s.replace(" ", "_") if s else "_"


_SNAKE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_SNAKE_2 = re.compile(r"([a-z0-9])([A-Z])")
_STRIP = re.compile(r"^k(?:Param|UIParam|RoutingDest|Routing)?")
_SEP = re.compile(r"[-\s./]+")


def snake(s):
    """kParamDetuneWid -> detune_wid ; kSoftClip -> soft_clip ; FormantONE -> formant_one
    Embedded-Data -> embedded_data"""
    s = _STRIP.sub("", str(s))
    s = _SEP.sub("_", s)
    s = _SNAKE_1.sub(r"\1_\2", s)
    s = _SNAKE_2.sub(r"\1_\2", s)
    s = re.sub(r"_+", "_", s)
    return s.lower().strip("_") or "_"


# nicer names where the auto form is ugly. rename only, never drop.
OVERRIDE = {
    # kParamWarp and kParamWarpMenu both snake toward 'warp'. The menu is the
    # mode name and deserves the short key; the float is the amount.
    "warp": "warp_amt",
    "warp_menu": "warp",
    "detune_wid": "detune_width",
    "limit_same_note_polyphony": "limit_same_note",
    "s1_compatibility": "s1_compat",
    "poly_count": "poly",
    "master_volume": "volume",
    "interpolate_after_load": "interp_after_load",
    "compensated_wet_dry": "wet_dry_comp",
}
FX_NAME_OVERRIDE = {"filter": "filterfx", "hyper_d": "hyper"}


def pname(k):
    n = snake(k)
    return OVERRIDE.get(n, n)


# ------------------------------------------------------------------- ledger

class Ledger:
    """Corpus information, not a gate. Records every structural key seen and
    whether this writer put it in the .idc, so the tail is visible instead of
    inferred."""

    def __init__(self):
        self.emitted = Counter()
        self.skipped = Counter()
        self.notes = Counter()
        self.events = []
        self.ctx = (0, "")

    def begin(self, idx, name):
        self.ctx = (idx, name)

    def event(self, kind, key, detail=""):
        """One decision, joinable against the restorer's log on (preset, key)."""
        if EVENT_LOG:
            self.events.append((self.ctx[0], self.ctx[1], kind, key, detail))

    def write_events(self, path):
        if not EVENT_LOG:
            return 0
        with open(path, "w", encoding="utf-8") as f:
            f.write("# idx\tpreset\tkind\tkey\tdetail\n")
            for idx, name, kind, key, detail in self.events:
                f.write(f"{idx}\t{name}\t{kind}\t{key}\t{detail}\n")
        return len(self.events)

    def emit(self, path):
        self.emitted[path] += 1

    def skip(self, path):
        self.skipped[path] += 1
        self.event("skip", path)

    def note(self, what):
        self.notes[what] += 1

    def report(self):
        out = ["# coverage", "", f"emitted key kinds : {len(self.emitted)}",
               f"skipped key kinds : {len(self.skipped)}", "",
               "## skipped, by frequency", ""]
        for k, n in self.skipped.most_common():
            out.append(f"  {n:>8}  {k}")
        out += ["", "## emitted, by frequency", ""]
        for k, n in self.emitted.most_common():
            out.append(f"  {n:>8}  {k}")
        if self.notes:
            out += ["", "## notes", ""]
            for k, n in self.notes.most_common():
                out.append(f"  {n:>8}  {k}")
        return "\n".join(out) + "\n"


LED = Ledger()

# ----------------------------------------------------------------- the init

# init.json is the scaffold. It does NOT hold resolved numeric defaults, it
# holds the same "default" sentinel Serum writes, so this is a structural
# comparison: is this module byte-identical to the module in a fresh instance.
# A module can be at init and still carry values (Env curves are 50/66.6/66.6
# in init), so emptiness is the wrong test and this is the right one.
INIT = {}


def at_init(data, key):
    return key in INIT and data.get(key) == INIT[key]


# --------------------------------------------------------------- primitives

def plain(mod):
    """plainParams, or {} when the module is at init."""
    if not isinstance(mod, dict):
        return {}
    pp = mod.get("plainParams")
    return pp if isinstance(pp, dict) else {}


def params_str(pp, owner, order=None):
    """plainParams dict -> 'k:v k:v', auto-named, deterministic order.
    String values are Serum enum members and get snaked too: kSoftClip ->
    soft_clip, FormantONE -> formant_one. Keeping the raw spelling would put
    two casings of the same concept in the vocabulary."""
    keys = list(pp.keys())
    if order:
        keys.sort(key=lambda k: (order.index(pname(k)) if pname(k) in order else 999, k))
    else:
        keys.sort()
    parts = []
    for k in keys:
        n = pname(k)
        v = pp[k]
        LED.emit(f"{owner}.{n}")
        parts.append(f"{n}:{snake(v) if isinstance(v, str) else fmt(v, n)}")
    return " ".join(parts)


def curve_points(cd):
    """{xVals,yVals,curveVals} -> 'x,y|c x,y|c ...'

    Any of the three can be a blob-ID STRING once the dedup pass has replaced a
    long array (serum2_schema.md 8 lists LFO curveData as a float-array payload
    class). Iterating one spells the id out a character per point, which is
    where points:W,W|W A,A|A V,V|V came from."""
    if not isinstance(cd, dict) or not cd.get("xVals"):
        return ""
    xs, ys = cd.get("xVals") or [], cd.get("yVals") or []
    cs = cd.get("curveVals") or []
    if any(isinstance(v, str) for v in (xs, ys, cs)):
        LED.emit("curve.blob_ref")
        return "@" + " ".join(v for v in (xs, ys, cs) if isinstance(v, str))
    parts = []
    for i, x in enumerate(xs):
        y = ys[i] if i < len(ys) else 0.0
        c = cs[i] if i < len(cs) else 0.5
        parts.append(f"{fmt(x, sig=CURVE_SIGFIGS)},{fmt(y, sig=CURVE_SIGFIGS)}"
                     f"|{fmt(c, sig=CURVE_SIGFIGS)}")
    return " ".join(parts)


def collapse(label, items, out):
    """identical blocks share one line: 'lfo3, lfo4 {..}'"""
    groups = defaultdict(list)
    for n, block in items:
        groups[block].append(n)
    for block, idxs in groups.items():
        out.append(f"{', '.join(f'{label}{n}' for n in idxs)} {block}")


# ------------------------------------------------------------------ writers

def write_header(meta, data, out):
    out.append("---")
    out.append(f"@preset {fmt(data.get('presetName') or meta.get('presetName') or '_')}")
    out.append(f"@author {fmt(data.get('presetAuthor') or '_')}")
    out.append("@category _")
    desc = (data.get("presetDescription") or "_")
    desc = re.sub(r"[\r\n]+", " ", str(desc)).strip() or "_"
    out.append(f"@comments {desc}")
    tags = data.get("tags") or []
    out.append("@tags " + (" ".join(snake(t) for t in tags) if tags else "_"))
    for i in range(1, 9):
        out.append(f"@macro{i} _")


def write_voice(data, out):
    g = {} if at_init(data, "Global0") else plain(data.get("Global0", {}))
    out.append(f"voice {{{params_str(g, 'voice') or '_'}}}")


# FX class ids, serum2_schema.md 7. Splitters put their children in the SAME
# flat FX list rather than nesting them, so a converter reading the rack as an
# ordered chain loses the grouping. Counted rather than hidden.


def fx_entries(data):
    """[(rack_index, entry)] across all three racks. Rack 0 is the chain."""
    out = []
    for r in range(3):
        rack = data.get(f"FXRack{r}") or {}
        fx = rack.get("FX") or []
        if fx and r:
            LED.note(f"fxrack{r}_non_empty")
        for e in fx:
            out.append((r, e))
    return out


def fx_name(entry):
    for k in entry:
        if k.startswith("FX"):
            n = snake(k[2:])
            return FX_NAME_OVERRIDE.get(n, n), k
    return None, None


def fx_label(r, entry):
    n = fx_name(entry)[0]
    return None if not n else (n if r == 0 else f"r{r+1}_{n}")


def write_chain(data, out):
    names = [fx_label(r, e) for r, e in fx_entries(data)]
    names = [n for n in names if n]
    out.append("effect_chain " + (" > ".join(names) if names else "_"))


# Mode is Oscillator{N}/plainParams/kParamType, a string, absent meaning
# wavetable. It is NOT which child is populated: all five children are present
# in every preset. serum2_schema.md 4, and serum2_idc.md 9.19 is a correction
# note written about exactly this mistake.
OSC_TYPE = {
    None: "WTOsc",
    "kOsc_Sample": "SampleOsc",
    "kOsc_Spectral": "SpectralOsc",
    "kOsc_MultiSample": "MultiSampleOsc",
    "kOsc_Granular": "GranularOsc",
}
ENGINE_TAG = {"WTOsc": "wt", "SampleOsc": "sample", "SpectralOsc": "spectral",
              "MultiSampleOsc": "multisample", "GranularOsc": "granular"}


def write_osc(data, i, out):
    mod = data.get(f"Oscillator{i}")
    label = f"osc{i+1}"
    if not isinstance(mod, dict) or at_init(data, f"Oscillator{i}"):
        out.append(f"{label} off _")
        return
    pp = dict(plain(mod))
    mode = pp.pop("kParamType", None)
    eng = OSC_TYPE.get(mode)
    if eng is None:
        LED.note(f"osc_type_unknown:{mode}")
        eng = "WTOsc"
    LED.note(f"osc_mode:{ENGINE_TAG[eng]}")
    sub = mod.get(f"{eng}{i}")
    lines = []
    if isinstance(sub, dict):
        write_engine(eng, sub, i + 1, lines)
    if not pp and eng == "WTOsc" and lines == [f"wavetable{i + 1} {{engine:wt}}"]:
        out.append(f"{label} off _")
        return
    out.append(f"{label} on {{{params_str(pp, 'osc') or '_'}}}")
    out.extend(lines)


def write_engine(eng, sub, n, out):
    bits = [f"engine:{ENGINE_TAG.get(eng, snake(eng))}"]
    isub = (INIT.get(f"Oscillator{n - 1}") or {}).get(f"{eng}{n - 1}") or {}
    default_table = (not sub.get("embeddedWTData") and str(sub.get("relativePathToWT") or "").lstrip("/") == str(isub.get("relativePathToWT") or "").lstrip("/")
                     and sub.get("numFrames") == isub.get("numFrames"))
    if default_table:
        pass
    elif sub.get("embeddedWTData"):
        bits.append("src:embedded")
        bits.append(f"name:{sub['embeddedWTData']}")
        LED.emit("wavetable.embedded")
    elif sub.get("relativePathToWT"):
        bits.append("src:path")
        bits.append(f"name:{str(sub['relativePathToWT']).lstrip('/').replace(' ', '_')}")
        LED.emit("wavetable.path")
    ps = params_str(plain(sub), "wavetable")
    if ps:
        bits.append(ps)
    nf = sub.get("numFrames")
    if isinstance(nf, (int, float)) and nf and not default_table:
        bits.append(f"frames:{fmt(nf / 2048)}")
        LED.emit("wavetable.frames")
        if nf % 2048:
            LED.note("numFrames_not_2048_aligned")
    if sub.get("tableDisplayName"):
        bits.append(f"display_name:{str(sub['tableDisplayName']).replace(' ', '_')}")
        LED.emit("wavetable.display_name")
    if "interpolateAfterLoad" in sub:
        bits.append(f"interp_after_load:{fmt(sub['interpolateAfterLoad'])}")
        LED.emit("wavetable.interp_after_load")
    out.append(f"wavetable{n} {{{' '.join(bits)}}}")
    flex = sub.get("flex")
    if isinstance(flex, dict) and flex.get("xVals") and (isinstance(flex["xVals"], str) or (flex.get("numPoints") or 0) > 1):
        pts = curve_points(flex)
        if pts:
            out.append(f"{{flex {pts}}}")
            LED.emit("wavetable.flex")
    for k in sub:
        if k in ("plainParams", "flex", "embeddedWTData", "relativePathToWT",
                 "numFrames", "numChannels", "sampleRate",
                 "interpolateAfterLoad", "tableDisplayName"):
            continue
        LED.skip(f"{eng}.{k}")


def write_noise_sub(data, out):
    n = None if at_init(data, "Oscillator3") else data.get("Oscillator3")
    pp = plain(n) if isinstance(n, dict) else {}
    inner = n.get("NoiseOsc3", {}) if isinstance(n, dict) else {}
    ip = plain(inner)
    if pp or ip:
        bits = params_str({**ip, **pp}, "noise")
        path = inner.get("relativePathToNoiseSample")
        ipath = ((INIT.get("Oscillator3") or {}).get("NoiseOsc3") or {}).get("relativePathToNoiseSample") or ""
        if path and str(path).lstrip("/") != str(ipath).lstrip("/"):
            bits = f"name:{str(path).lstrip('/').replace(' ', '_')} " + bits
        out.append(f"noise on {{{bits or '_'}}}")
    else:
        out.append("noise off _")
    s = None if at_init(data, "Oscillator4") else data.get("Oscillator4")
    sp = plain(s) if isinstance(s, dict) else {}
    si = plain(s.get("SubOsc4", {})) if isinstance(s, dict) else {}
    if sp or si:
        out.append(f"sub on {{{params_str({**si, **sp}, 'sub') or '_'}}}")
    else:
        out.append("sub off _")


def write_filters(data, out):
    for i in (0, 1):
        pp = {} if at_init(data, f"VoiceFilter{i}") else plain(data.get(f"VoiceFilter{i}", {}))
        if pp:
            out.append(f"filter{i+1} on {{{params_str(pp, 'filter')}}}")
        else:
            out.append(f"filter{i+1} off _")


# RoutingSlot0..6, one per mixer channel strip. 0/1/2 confirmed as OSC A/B/C
# and 5 as FILTER 1, across two presets. 3, 4 and 6 are NOISE / SUB / FILTER 2
# in an order that is NOT confirmed, so they keep their slot number rather than
# a name that would read as measured. serum2_schema.md 6.
ROUTING_SLOTS = ["osc_a", "osc_b", "osc_c", "slot3", "slot4",
                 "filter1", "slot6"]


def write_routing(data, out):
    bits, extra = [], []
    for i, label in enumerate(ROUTING_SLOTS):
        pp = plain(data.get(f"RoutingSlot{i}", {}))
        if not pp:
            bits.append(f"{label}:default")
            continue
        dest = pp.get("kParamRoutingDest")
        bits.append(f"{label}:{snake(dest) if dest else 'default'}")
        LED.emit("routing.dest")
        for k in sorted(pp):
            if k != "kParamRoutingDest":
                extra.append(f"{label}.{pname(k)}:{fmt(pp[k], k)}")
    out.append(f"routing {{{' '.join(bits + extra)}}}")


ENV_ORDER = ["attack", "hold", "decay", "sustain", "release",
             "curve1", "curve2", "curve3"]


def write_envs(data, out):
    items, off = [], []
    for i in range(4):
        pp = {} if at_init(data, f"Env{i}") else plain(data.get(f"Env{i}", {}))
        ipp = plain(INIT.get(f"Env{i}", {}))
        pp = {k: v for k, v in pp.items() if k not in ipp or fmt(v) != fmt(ipp[k])}
        if pp:
            items.append((i + 1, f"{{{params_str(pp, 'env', ENV_ORDER)}}}"))
        else:
            off.append((i + 1, "unrouted _"))
    collapse("env", items, out)
    collapse("env", off, out)


def write_lfos(data, out):
    items, off = [], []
    for i in range(10):
        mod = data.get(f"LFO{i}")
        if not isinstance(mod, dict) or at_init(data, f"LFO{i}"):
            off.append((i + 1, "unrouted _"))
            continue
        pp = plain(mod)
        pts = curve_points(mod.get("curveData") or {})
        if not pp and not pts:
            off.append((i + 1, "unrouted _"))
            continue
        blk = f"{{{params_str(pp, 'lfo') or '_'}}}"
        if pts:
            loop = (mod.get("curveData") or {}).get("loopbackPointNum")
            lp = f"loop:{fmt(loop)} " if loop is not None else ""
            blk += f" {{shape:custom {lp}points:{pts}}}"
            LED.emit("lfo.shape")
        if (mod.get("pathData") or {}):
            LED.skip("LFO.pathData")
        items.append((i + 1, blk))
    collapse("lfo", items, out)
    collapse("lfo", off, out)


# Which parameters carry an effect's audibility. Per-effect plugin knowledge;
# it does NOT generalise, which is why it is written out by hand.
#
# FXHyperD is why. It is two effects in one, Hyper and Dimension, with TWO
# independent wet controls. A generic "kParamWet == 0 means dead" rule called
# BA Marble Power dead when it was audible entirely through kParamDimEWet at
# 72.37. Paid for 2026-08-19.
AUDIBILITY = {
    "FXHyperD":     ("kParamWet", "kParamDimEWet"),
    "FXChorus":     ("kParamWet",),
    "FXDelay":      ("kParamWet",),
    "FXDistortion": ("kParamWet",),
    "FXFilter":     ("kParamWet",),
    "FXFlanger":    ("kParamWet",),
    "FXPhaser":     ("kParamWet",),
    "FXReverb":     ("kParamWet",),
    "FXComp":       ("kParamWet",),
    "FXEQ":         (),
    "FXBode":       ("kParamWet",),
    "FXConv":       ("kParamWet",),
    "FXUtils":      ("kParamWet",),
    "FXSplit":      (),
    "FXSplit3":     (),
    "FXSplitMS":    (),
}


def modulated_dests(data):
    """{'FXReverb/3/kParamWet', ...} for every destination a live slot can
    actually drive. A zero sitting under a modulator is a starting value, not
    a dead control, so normalisation has to consult this first."""
    out = set()
    for i in range(64):
        s = data.get(f"ModSlot{i}")
        if not isinstance(s, dict):
            continue
        pp = s.get("plainParams")
        if not isinstance(pp, dict):
            continue
        src = (s.get("source") or [0])[0]
        if not pp.get("kParamAmount") and src not in USER_SOURCE_IDS:
            continue
        out.add(f"{s.get('destModuleTypeString')}/{s.get('destModuleID')}"
                f"/{s.get('destModuleParamName')}")
    return out


# Player input: the performer supplies the amount at play time, so a zero here
# is a starting position rather than a dead route.
USER_SOURCE_IDS = {1, 16, 17, 18, 19, 33, 34, 35, 36, 37} | set(range(25, 33))


def fx_audible(cls, pp, rack_slot, moddests):
    key = f"{cls}/{rack_slot}"
    controls = AUDIBILITY.get(cls)
    if controls is None:
        LED.note(f"fx_no_audibility_knowledge:{cls}")
        LED.event("audible", key, f"no knowledge for {cls}, assumed audible")
        return True
    if not controls:
        LED.event("audible", key, "no wet control on this class")
        return True
    vals, mods = [], []
    for c in controls:
        v = pp.get(c)
        vals.append(f"{c}={'absent' if v is None else fmt(v)}")
        m = f"{cls}/{rack_slot}/{c}" in moddests
        mods.append(f"{c}:mod={int(m)}")
        if v is None or v != 0.0 or m:
            LED.event("audible", key, f"{' '.join(vals)} | {' '.join(mods)}")
            return True
    LED.event("inaudible", key, f"{' '.join(vals)} | {' '.join(mods)}")
    return False


# The ten-effect fixed frame (owner ruling 2026-08-19, amended 2026-08-20):
# every preset carries all ten effect lines. Racked rack-0 effects are emitted
# first in effect_chain order; the unracked remainder follows as `off _` lines
# in this list's order, alphabetical by effect key, so the trailing order is
# the same in every preset. r2_/r3_ rack entries are outside the frame and keep
# their own labels.
FX_FRAME = ["chorus", "comp", "delay", "distortion", "eq",
            "filterfx", "flanger", "hyper", "phaser", "reverb"]


def write_fx(data, out):
    moddests = modulated_dests(data)
    rack_pos = Counter()
    racked = set()
    for r, e in fx_entries(data):
        name, key = fx_name(e)
        if not name:
            continue
        slot = rack_pos[r]
        rack_pos[r] += 1
        name = fx_label(r, e)
        if r == 0:
            racked.add(name)
        pp = plain(e.get(key, {}))
        if pp.get("kParamEnable", 1.0) == 0.0:
            LED.note(f"fx_off_explicit:{key}")
            out.append(f"{name} off _")
            continue
        if not fx_audible(key, pp, slot, moddests):
            LED.note(f"fx_normalised_off:{key}")
            out.append(f"{name} off _")
            continue
        body = params_str(pp, f'fx.{name}')
        ir = (e.get(key) or {}).get("relativePathToIR")
        if ir:
            body = f"ir:{str(ir).replace(' ', '_')}" + (f" {body}" if body else "")
        out.append(f"{name} on {{{body or '_'}}}")
        for k in e:
            if k == key or k == "type":
                continue
            LED.skip(f"FX.{k}")
        for k in e.get(key, {}):
            if k != "plainParams":
                LED.skip(f"{key}.{k}")
    for n in FX_FRAME:
        if n not in racked:
            out.append(f"{n} off _")


# Source ids 0-59, read off Serum's UI from generated presets.
# serum2_schema.md 5 "Source ids - complete, 0-59"; provenance serum2_idc.md
# 9.15 / 9.17. Out-of-range ids clamp to 59 rather than erroring, so a writer
# must range-check itself.
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

_MOD_HANDLED = {"kParamAmount", "kParamBipolar", "kParamBypass",
                "kParamCurveIn", "kParamAuxInverted", "kParamAuxCurve",
                "kParamAuxCurveData", "kParamOut"}


def source_name(v):
    if isinstance(v, int) and v in SOURCE_IDS:
        return SOURCE_IDS[v]
    LED.note(f"source_id_unknown:{v}")
    return f"src{v}"


MOD_MODULE = {
    "Oscillator": "osc", "WTOsc": "osc", "GranularOsc": "osc",
    "MultiSampleOsc": "osc", "SampleOsc": "osc", "SpectralOsc": "osc",
    "SubOsc": "sub", "NoiseOsc": "noise",
    "VoiceFilter": "filter", "Env": "env", "LFO": "lfo", "Macro": "macro",
}


# destModuleID on an FX encodes the rack in its hundreds digit: rack = id//100,
# slot = id%100. INFERRED, not measured off the plugin. Evidence: five rows in
# the corpus carry ids 100, 200 and 201, and they land exactly where FXRack1
# and FXRack2 are non-empty. Writing the id away loses them, which is what a
# bare snake(type) fallback was doing.
FX_RACK_STRIDE = 100


def mod_dest(slot, fxnames_by_rack):
    t = slot.get("destModuleTypeString") or ""
    mid = slot.get("destModuleID")
    pn = pname(slot.get("destModuleParamName") or "_")
    if t.startswith("FX"):
        if isinstance(mid, int):
            rack, pos = divmod(mid, FX_RACK_STRIDE)
            names = fxnames_by_rack.get(rack) or []
            if 0 <= pos < len(names) and names[pos]:
                LED.emit(f"mod_dest.rack{rack}")
                return names[pos] + f"_{pn}"
        LED.note(f"mod_dest_unresolved_fx:{t}[{mid}]")
        return f"{snake(t)}_{pn}"
    base = MOD_MODULE.get(t, snake(t))
    # NoiseOsc and SubOsc are singletons but their module ids are 3 and 4, not
    # 0, so the id is not redundant. It is not written here because the name
    # already implies it; the restorer puts it back from FIXED_MODULE_ID.
    if base in ("sub", "noise"):
        return f"{base}_{pn}"
    idx = (mid + 1) if isinstance(mid, int) else 1
    return f"{base}{idx}_{pn}"


def write_mods(data, out):
    fxnames = defaultdict(list)
    for r, e in fx_entries(data):
        fxnames[r].append(fx_label(r, e))
    out.append("modulations")
    n = 0
    for i in range(64):
        slot = data.get(f"ModSlot{i}")
        if not isinstance(slot, dict):
            continue
        pp = plain(slot)
        if not pp and "source" not in slot:
            continue
        src = slot.get("source")
        # [primary, aux], both from the id space, aux 0 = none.
        prim = src[0] if isinstance(src, list) and src else 0
        aux = src[1] if isinstance(src, list) and len(src) > 1 else 0
        stag = source_name(prim) + (f" + {source_name(aux)}" if aux else "")
        LED.note(f"mod_source:{source_name(prim)}")
        dest = mod_dest(slot, fxnames)
        tags = f"{dest}:{fmt(pp.get('kParamAmount', 0.0))}"
        for k, lab in (("kParamBipolar", "bipolar"),
                       ("kParamBypass", "bypass"), ("kParamCurveIn", "curve_in"),
                       ("kParamAuxInverted", "aux_inverted"),
                       ("kParamAuxCurve", "aux_curve"),
                       ("kParamAuxCurveData", "aux_curve_data"),
                       ("kParamOut", "out")):
            if k in pp:
                tags += f" {lab}:{fmt(pp[k])}"
        for k in pp:
            if k not in _MOD_HANDLED:
                LED.skip(f"ModSlot.{k}")
        n += 1
        out.append(f"[{n}] {stag} -> {{{tags}}}")
    if n == 0:
        out.append("_")


SCALAR_IDENTITY = "0,1|0.5 1,0|0.5"


def write_scalars(data, out):
    sc = data.get("scalars") if isinstance(data.get("scalars"), dict) else {}
    for key, label in (("velo", "velo_curve"), ("note", "note_curve")):
        cd = sc.get(key)
        if not isinstance(cd, dict) or not cd:
            continue
        pts = curve_points(cd)
        parts = []
        if cd.get("legato", True) is False:
            parts.append("legato:0")
        if pts and pts != SCALAR_IDENTITY:
            parts.append(f"points:{pts}")
        if parts:
            out.append(f"{label} {{{' '.join(parts)}}}")


def write_macros(data, out):
    for i in range(8):
        pp = plain(data.get(f"Macro{i}", {}))
        v = pp.get("kParamValue", 0.0) if pp else 0.0
        out.append(f"macro_control_{i+1} {fmt(v)}")


HANDLED_PREFIX = ("Oscillator", "VoiceFilter", "Env", "LFO", "ModSlot",
                  "Macro", "RoutingSlot", "FXRack", "Global")
IGNORE_EXACT = {
    "fileType", "product", "productVersion", "url", "vendor", "version",
    "presetName", "presetAuthor", "presetDescription", "tags",
    "SerumGUI", "Filter", "Osc", "WTOsc", "GranularOsc", "SpectralOsc",
    "MultiSampleOsc", "ClipPlayer", "arpBankDisplayName", "clipBankDisplayName",
}


def convert_preset(p, idx=0):
    data = p.get("data") or {}
    meta = p.get("metadata") or {}
    LED.begin(idx, str(data.get("presetName")
                      or meta.get("presetName") or f"preset_{idx}"))
    out = []
    write_header(meta, data, out)
    out.append("")
    write_voice(data, out)
    write_chain(data, out)
    out.append("")
    for i in range(3):
        write_osc(data, i, out)
    write_noise_sub(data, out)
    out.append("")
    write_filters(data, out)
    write_routing(data, out)
    out.append("")
    write_envs(data, out)
    out.append("")
    write_lfos(data, out)
    out.append("")
    write_fx(data, out)
    out.append("")
    write_mods(data, out)
    out.append("")
    write_macros(data, out)
    write_scalars(data, out)

    # Anything at top level this writer never looked at. A module sitting at
    # init is not a gap, so it must not be counted as one: otherwise every
    # untouched Arp and MidiClip reports once per preset and buries the real
    # tail under identical 5997s.
    for k in data:
        if k in IGNORE_EXACT or k.startswith(HANDLED_PREFIX):
            continue
        if at_init(data, k):
            continue
        v = data[k]
        if isinstance(v, dict) and v.get("plainParams") == "default" and len(v) == 1:
            continue
        LED.skip(f"top.{k}")
    return "\n".join(out)


# ------------------------------------------------------------- shape refmap

BASE_WORDS = [
    "silk", "rust", "bone", "crisp", "nitro", "haze", "veil", "edge", "peak",
    "tide", "pulse", "drift", "root", "fold", "press", "burn", "carve",
    "forge", "trace", "mark", "bind", "draw", "pour", "rise", "sharp",
    "hollow", "bright", "heavy", "light", "still", "swift", "rough",
    "narrow", "fierce", "obsidian", "tallow", "resin", "flint", "chalk",
    "loom", "husk", "burl", "tine", "weft", "clay", "jinx", "nexus", "keel",
    "pixel", "umbra", "zinc", "etched", "quartz", "joule", "fulcrum",
    "ibex", "moss", "patina", "stochastic", "sentinel",
]

_SHAPE_RE = re.compile(r"\{shape:custom\s+(?:loop:(\S+)\s+)?points:([^}]*)\}")


def mint(points, taken):
    h = hashlib.md5(points.encode("utf-8")).hexdigest()
    t = len(BASE_WORDS)
    off = 0
    while off < t * t:
        a = BASE_WORDS[int(h[0:4], 16) % t]
        b = BASE_WORDS[(int(h[4:8], 16) + off) % t]
        name = f"LFOSHAPE_{a}_{b}"
        if name not in taken:
            return name
        off += 1
    raise RuntimeError("shape id space exhausted")


def build_shape_refmap(lines, min_uses=2):
    counts = Counter()
    for l in lines:
        m = _SHAPE_RE.search(l)
        if m:
            counts[m.group(2).strip()] += 1
    refmap, taken = {}, set()
    pts_to_id = {}
    for pts, n in counts.most_common():
        if n < min_uses:
            continue
        name = mint(pts, taken)
        taken.add(name)
        pts_to_id[pts] = name
        refmap[name] = pts
    return refmap, pts_to_id, counts


def apply_shape_refmap(lines, pts_to_id):
    out = []
    for l in lines:
        def sub(m):
            pts = m.group(2).strip()
            sid = pts_to_id.get(pts)
            if not sid:
                return m.group(0)
            loop = f"loop:{m.group(1)} " if m.group(1) else ""
            return f"{{shape:{sid} {loop}}}".replace("  ", " ").replace(" }", "}")
        out.append(_SHAPE_RE.sub(sub, l))
    return out


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", nargs="?",
                    default=os.path.join(HERE, "serum2_presets.jsonl"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--log", action="store_true",
                    help=f"write {os.path.basename(EVENT_LOG_PATH)} "
                         f"(same as setting EVENT_LOG = True in this file)")
    a = ap.parse_args()

    global EVENT_LOG
    if a.log:
        EVENT_LOG = True

    inp = a.infile
    out_path = a.out or os.path.join(HERE, "serum2_presets.idc")
    refmap_path = out_path.replace(".idc", "_shape_refmap.json")
    cov_path = out_path.replace(".idc", "_coverage.md")

    if not os.path.exists(inp):
        print(f"not found: {inp}")
        return 1

    global INIT
    init_path = os.path.join(HERE, "init.json")
    if os.path.exists(init_path):
        with open(init_path, encoding="utf-8") as f:
            INIT = (json.load(f) or {}).get("data") or {}
        print(f"init scaffold: {len(INIT)} modules from {init_path}")
    else:
        print(f"WARNING: no init.json at {init_path}. Every module will read as "
              f"changed and the .idc will be much larger than it should be.")

    print(f"reading {inp}")
    all_lines, ok, bad = [], 0, 0
    with open(inp, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if a.limit and ok >= a.limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                p = json.loads(line)
                all_lines.extend(convert_preset(p, ok + 1).splitlines())
                all_lines.append("")
                ok += 1
            except Exception as e:
                bad += 1
                print(f"  line {i+1}: {type(e).__name__}: {e}")
            if ok and ok % 1000 == 0:
                print(f"  {ok} converted")
    print(f"pass 1: {ok} converted, {bad} failed")

    refmap, pts_to_id, counts = build_shape_refmap(all_lines)
    reused = sum(n for p, n in counts.items() if n >= 2)
    print(f"pass 2: {len(counts)} distinct lfo shapes, "
          f"{len(refmap)} used 2+ times covering {reused} occurrences")

    final = apply_shape_refmap(all_lines, pts_to_id)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(final))
    with open(refmap_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(refmap.items())), f, indent=1)
    with open(cov_path, "w", encoding="utf-8") as f:
        f.write(LED.report())

    n_ev = LED.write_events(EVENT_LOG_PATH)
    if n_ev:
        print(f"events   {EVENT_LOG_PATH}  ({n_ev} rows)")

    sz = os.path.getsize(out_path)
    print(f"\nwritten  {out_path}  ({sz/1e6:.1f} MB, {len(final)} lines)")
    print(f"refmap   {refmap_path}  ({len(refmap)} ids)")
    print(f"coverage {cov_path}")
    if ok:
        print(f"\nper preset: {len(final)/ok:.1f} lines, {sz/ok:.0f} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
