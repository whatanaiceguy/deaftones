import math, re
from pathlib import Path

import h2p

HERE = Path(__file__).parent
INIT = h2p.parse((HERE / "init.h2p").read_bytes())
DEFAULT = INIT.values()

SRC = ["none", "modwhl", "pitchw", "ctrla", "ctrlb", "gate", "velocity", "pressure", "keyfollow", "keyfollow2",
       "alternate", "random", "stackvoice", "voicemap", "env1", "env2", "lfo1", "lfo2", "rectify", "invert",
       "quant", "lag", "adder", "multiply"]
SYNC = {-3: "0.1s", -2: "1s", -1: "10s", 0: "1/64", 1: "1/32", 2: "1/16", 3: "1/8", 4: "1/4", 5: "1/2", 6: "1/1",
        7: "1/32dot", 8: "1/16dot", 9: "1/8dot", 10: "1/4dot", 11: "1/2dot", 12: "1/16trip", 13: "1/8trip",
        14: "1/4trip", 15: "1/2trip", 16: "1/1trip", 17: "2/1", 18: "3/1", 19: "4/1", 20: "5/1", 21: "6/1",
        22: "7/1", 23: "8/1"}
FREE = {-3: 10.0, -2: 1.0, -1: 0.1}
E = {
    "onoff": ["off", "on"],
    "voices": ["2", "3", "4", "5", "6", "8", "12", "16"],
    "vmode": ["poly", "mono", "legato", "duo", "poly2"],
    "glidemode": ["time", "rate"],
    "priority": ["last", "lowest", "highest"],
    "envmodel": ["ads", "analogue", "digital"],
    "trig1": ["gate", "loop", "lfo1", "lfo2"],
    "trig2": ["gate", "single", "loop", "lfo1", "lfo2"],
    "restart": ["sync", "gate", "single", "random"],
    "wave": ["sine", "triangle", "saw_up", "saw_down", "sqr_hi_lo", "sqr_lo_hi", "rand_hold", "rand_glide"],
    "polarity": ["bipolar", "positive"],
    "oscmodel": ["triple_vco", "dual_vco", "dco", "dual_vco_eco", "digital"],
    "pulse": ["off", "square", "narrow", "pwm"],
    "saw": ["off", "saw", "saw2", "pwm_saw", "spikes", "spikes_gap"],
    "sub": ["oct1_a", "oct1_b", "oct1_c", "oct1_d", "oct2_a", "oct2_b"],
    "trishape": ["ideal", "analog1", "analog2"],
    "noisecolor": ["white", "pink"],
    "tmmode": ["2>1,1>1", "1>1,2>1,1>2,2>2", "2>2,1>2", "1>1,2>2"],
    "digital": ["multisaw", "triwrap", "noise", "feedback", "pulse", "saw", "triangle"],
    "hpfmodel": ["feedback", "post", "pre", "bite"],
    "vcfmodel": ["ladder", "cascade", "multimode", "bite", "uhbie"],
    "laddermode": ["24db", "12db"],
    "laddercolor": ["clean", "rough"],
    "svf": ["lp24", "lp12", "hp", "bp"],
    "uhbie": ["bandreject", "bandpass"],
    "vca": ["gate", "env1"],
    "chorus": ["classic", "dramatic", "ensemble"],
    "phaser": ["stoned", "flanged"],
    "rotary": ["normal", "syncbass", "nobass"],
    "rotctl": ["modwheel", "ctrla", "ctrlb", "pressure"],
    "arpdir": ["played", "up", "down", "updown1", "updown2", "random"],
    "arporder": ["serial", "round", "leap", "repeat"],
    "arprestart": ["none", "4", "5", "6", "7", "8", "9", "10", "12", "14", "16", "24", "32"],
    "timebase": ["1/32", "1/16", "1/8", "1/4"],
    "transient": ["analog", "dc_reset", "vcf_reset", "osc_reset"],
}
MODULES = ["Chorus", "Phaser", "Plate", "Delay", "Rotary"]
FXSEC = {"Chorus": "Chrs", "Phaser": "Phase", "Plate": "Plate", "Delay": "Delay", "Rotary": "Rtary"}

BLOCKS = [
    ("voice", "VCC", [("Voices", "voices", "e:voices"), ("Voicing", "stack", "i"), ("Mode", "mode", "e:vmode"),
                      ("PortaM", "glide_mode", "e:glidemode"), ("Porta", "glide", "f"), ("Porta2", "glide2", "f"),
                      ("PRange", "glide_range", "f"), ("PB", "bend_up", "st"), ("PBD", "bend_down", "st"),
                      ("Drft", "microtuning", "e:onoff"), ("Trsp", "transpose", "st"), ("FTun", "fine", "ct"),
                      *[(f"Vc{i}", f"stack_tune{i}", "st") for i in range(1, 9)], ("Prior", "priority", "e:priority")]),
    ("osc", "OSC", [("Tune1", "tune1", "st"), ("Tune2", "tune2", "st"), ("Tune3", "tune3", "st"), ("VtoD", "vibrato", "f"),
                    ("PW", "pulse_width", "f"), ("Shape1", "shape1", "f"), ("Shape2", "shape2", "f"), ("Shape3", "shape3", "f"),
                    ("FM", "fm", "f"), ("Sync2", "sync2", "e:onoff"), ("OscMix", "mix", "f"),
                    ("Vol1", "volume1", "db20"), ("Vol2", "volume2", "db20"), ("Vol3", "volume3", "db20"),
                    ("PWShp", "pulse_shape", "e:pulse"), ("SawShp", "saw_shape", "e:saw"), ("SubShp", "sub_shape", "e:sub"),
                    ("Tri1", "tri1", "e:onoff"), ("Sin2", "sine2", "e:onoff"), ("Saw1", "saw1", "e:onoff"), ("PWM1", "pwm1", "e:onoff"),
                    ("Tri2", "tri2", "e:onoff"), ("Saw2", "saw2", "e:onoff"), ("PW2", "pulse2", "e:onoff"), ("PWM2", "pwm2", "e:onoff"),
                    ("Noise1", "noise1", "e:onoff"), ("triSp", "tri_shape", "e:trishape"), ("Sync3", "sync3", "e:onoff"),
                    ("Noise", "noise", "db40"), ("NoiseC", "noise_color", "e:noisecolor"),
                    ("TM1On", "tunemod_osc1", "e:onoff"), ("TM2On", "tunemod_osc2", "e:onoff"), ("TM3On", "tunemod_osc3", "e:onoff"),
                    ("SM1On", "shapemod_osc1", "e:onoff"), ("SM2On", "shapemod_osc2", "e:onoff"), ("SM3On", "shapemod_osc3", "e:onoff"),
                    ("TMmode", "tunemod_mode", "e:tmmode"), ("Wave1", "eco_wave1", "f"), ("Wave2", "eco_wave2", "f"),
                    ("RMPwm", "ringmod", "e:onoff"), ("Drift", "drift", "f"), ("JpSp1", "jpsp1", "i"), ("JpSp2", "jpsp2", "i"),
                    ("BpNoise", "bipolar_noise", "e:onoff"), ("RstPh1", "reset_phase1", "f"), ("RstPh2", "reset_phase2", "f"),
                    ("RstPh3", "reset_phase3", "f"), ("DgtlTp1", "digital_type1", "e:digital"), ("DgtlSp2", "digital_shape2", "f"),
                    ("DgtlTp2", "digital_type2", "e:digital"), ("DgtlSp3", "digital_shape3", "f"), ("DgtlSp4", "digital_shape4", "f"),
                    ("DNoAlia", "digital_antialias", "e:onoff")]),
    ("hpf", "HPF", [("Freq", "cutoff", "hz"), ("Res", "resonance", "f"), ("Rev", "revision", "i"), ("KeyScl", "keyfollow", "f"),
                    ("PFreq", "post_cut", "i")]),
    ("filter", "VCF1", [("Freq", "cutoff", "hz"), ("Res", "resonance", "f"), ("KeyScl", "keyfollow", "f"), ("FFM", "filter_fm", "f"),
                        ("LMode", "slope", "e:laddermode"), ("LColor", "color", "e:laddercolor"), ("SkRev", "revision", "i"),
                        ("SvfMode", "mode", "e:svf"), ("Fback", "feedback", "f"), ("InTune", "in_tune", "e:onoff"),
                        ("ShpMix", "mix", "f"), ("BpUbie", "center", "e:uhbie")]),
    ("vca", "VCA1", [("Pan", "pan", "f"), ("Vol", "volume", "db20"), ("VCA", "env", "e:vca"), ("Mode", "mode", "i"), ("Offset", "offset", "f")]),
    ("env1", "ENV1", None), ("env2", "ENV2", None), ("lfo1", "LFO1", None), ("lfo2", "LFO2", None),
    ("modifiers", "MOD", [("Quant", "quantize_steps", "f"), ("Slew", "lag_rate", "f"), ("RSrc", "rectify", "src"), ("ISrc", "invert", "src"),
                          ("QSrc", "quantize", "src"), ("LSrc", "lag", "src"), ("A1Src", "add1", "src"), ("A2Src", "add2", "src"),
                          ("M1Src", "mul1", "src"), ("M2Src", "mul2", "src")]),
    ("trimmers", "OPT", [*[(f"V{v}T{t}", f"v{v}_osc{t}_detune", "f") for t in (1, 2, 3) for v in range(1, 9)],
                         ("TSlop", "detune_amount", "f"), ("RndC", "rnd_cutoff", "i"), ("CSlop", "cutoff_var", "f"),
                         ("RndG", "rnd_glide", "i"), ("GSlop", "glide_var", "f"), ("RndP", "rnd_pw", "i"), ("PSlop", "pw_var", "f"),
                         ("RndE", "rnd_env", "i"), ("ESlop", "env_var", "f"),
                         *[(f"V{v}Mod", f"voicemap{v}", "f") for v in range(1, 9)], ("TransM", "transient", "e:transient")]),
    ("arp", "ARP", None),
]
ENVKEYS = [("Atk", "attack", "f"), ("Dec", "decay", "f"), ("Sus", "sustain", "f"), ("Rel", "release", "f"), ("Vel", "velocity", "f"),
           ("Quant", "quantize", "e:onoff"), ("Crve", "curve", "e:onoff"), ("RelOn", "release_on", "e:onoff"), ("KeyFlw", "keyfollow", "f")]
LFOKEYS = [("Trig", "restart", "e:restart"), ("Wave", "wave", "e:wave"), ("Phse", "phase", "f"), ("Polar", "polarity", "e:polarity"),
           ("Dly", "delay", "f"), ("DMS1", "depth_src", "src"), ("DMD1", "depth_amount", "f")]
ARPKEYS = [("Dir", "direction", "e:arpdir"), ("Oct", "octaves", "i"), ("CDiv", "cdiv", "i"), ("Rstrt", "restart", "e:arprestart"),
           ("Order", "order", "e:arporder"), ("CLK.Mult", "clock_mult", "f"), ("CLK.Base", "timebase", "e:timebase"),
           ("CLK.Swing", "swing", "f"), ("CLK.Strict", "strict", "i")]
FXKEYS = {
    "Chorus": [("Type", "type", "e:chorus"), ("Rate", "rate", "f"), ("Depth", "depth", "f"), ("Wet", "wet", "f")],
    "Phaser": [("Type", "type", "e:phaser"), ("Rate", "rate", "f"), ("FB", "feedback", "f"), ("Stereo", "stereo", "f"), ("Sync", "sync", "e:onoff"),
               ("Phase", "phase", "f"), ("Wet", "wet", "f"), ("Depth", "depth", "f"), ("Center", "center", "f")],
    "Plate": [("Pre", "predelay", "f"), ("Diff", "diffusion", "f"), ("Damp", "damp", "f"), ("Decay", "decay", "f"), ("Size", "size", "f"),
              ("Dry", "dry", "f"), ("Wet", "wet", "f")],
    "Delay": [("LDel", "left", "x16"), ("CDel", "center", "x16"), ("RDel", "right", "x16"), ("SVol", "side_vol", "f"), ("CVol", "center_vol", "f"),
              ("FeedB", "feedback", "f"), ("HP", "hp", "f"), ("LP", "lp", "f"), ("Dry", "dry", "f"), ("Wow", "wow", "f")],
    "Rotary": [("Mode", "mode", "e:rotary"), ("Mix", "mix", "f"), ("Bal", "balance", "f"), ("Drive", "drive", "f"), ("Stereo", "stereo", "f"),
               ("Out", "out", "f"), ("Slow", "slow", "f"), ("Fast", "fast", "f"), ("RTime", "rise", "f"), ("Cont", "controller", "e:rotctl")],
}
ROUTES = [
    ("OSC", "TM1Src", "TM1Dpt", "osc_tunemod1", "st"), ("OSC", "TM2Src", "TM2Dpt", "osc_tunemod2", "st"),
    ("OSC", "PWMSrc", "PWMDpt", "osc_pulse_width", "f"), ("OSC", "Sh1Src", "Sh1Dpt", "osc_shape", "f"),
    ("OSC", "FmSrc", "FmDpt", "osc_fm", "f"), ("OSC", "NoisSrc", "NoisDpt", "osc_noise_mix", "f"),
    ("HPF", "FMSrc", "FMDpt", "hpf_cutoff", "st"),
    ("VCF1", "FMSrc", "FMDpt", "filter_cutoff", "st"), ("VCF1", "FM2Src", "FM2Dpt", "filter_cutoff2", "st"),
    ("VCF1", "ResSrc", "ResDpt", "filter_resonance", "f"), ("VCF1", "FfmSrc", "FfmDpt", "filter_fm", "f"),
    ("VCF1", "FbSrc", "FbDpt", "filter_feedback", "f"), ("VCF1", "SMSrc", "SMDpt", "filter_mix", "f"),
    ("VCA1", "ModSrc", "ModDpt", "vca_volume", "f"), ("VCA1", "PanSrc", "PanDpt", "vca_pan", "f"),
    ("LFO1", "FMS1", "FMD1", "lfo1_rate", "oct"), ("LFO2", "FMS1", "FMD1", "lfo2_rate", "oct"),
]
DROPPED = {("VCC", "MuCore"), ("VCC", "ContEn"), ("VCC", "MTunS"), ("VCC", "MTunN"), ("VCC", "MTunT"), ("VCC", "rMW"), ("VCC", "rPW"),
           ("OPT", "Acc"), ("OPT", "OffAcc"), ("OPT", "TPots"), ("OPT", "CtrPge")}


def num(s):
    return float(s)


def trim(x):
    t = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


def hz(k):
    return 440.0 * 2 ** ((k - 81.0) / 12.0)


def db(k, p):
    return float("-inf") if k <= 0 else p * math.log10(k / 100.0)


def fmt_unit(kind, raw):
    if kind.startswith("e:"):
        names = E[kind[2:]]
        i = int(round(num(raw)))
        return names[i] if 0 <= i < len(names) else f"#{i}"
    if kind == "src":
        i = int(raw)
        return SRC[i] if 0 <= i < len(SRC) else f"#{i}"
    if kind == "i":
        return str(int(num(raw)))
    k = num(raw)
    if kind == "hz":
        return f"{hz(k):.2f}Hz"
    if kind in ("db20", "db40"):
        v = db(k, 20 if kind == "db20" else 40)
        return "-infdB" if v == float("-inf") else f"{v:.2f}dB"
    if kind == "st":
        return trim(k) + "st"
    if kind == "ct":
        return trim(k) + "ct"
    if kind == "oct":
        return trim(k) + "oct"
    if kind == "x16":
        return trim(k) + "/16"
    return trim(k)


def parse_unit(kind, s, like):
    is_int = "." not in like
    if kind.startswith("e:"):
        names = E[kind[2:]]
        i = int(s[1:]) if s.startswith("#") else names.index(s)
        return str(i) if is_int else f"{i:.2f}"
    if kind == "src":
        return str(int(s[1:]) if s.startswith("#") else SRC.index(s))
    if kind == "i":
        return s if is_int else f"{int(s):.2f}"
    if kind in ("hz", "db20", "db40"):
        if s == "-infdB":
            return "0.00"
        v = float(re.sub(r"(Hz|dB)$", "", s))
        x = (81.0 + 12.0 * math.log2(v / 440.0)) if kind == "hz" else 100.0 * 10 ** (v / (20 if kind == "db20" else 40))
        base = round(x, 2)
        hits = [round(base + d * 0.01, 2) for d in range(-8, 9)]
        hits = [c for c in hits if fmt_unit(kind, f"{c:.2f}") == s]
        if not hits:
            return f"{base:.2f}"
        nice = lambda c: next(i for i, q in enumerate((100, 20, 10, 2, 1)) if round(c * 100) % q == 0)
        return f"{min(hits, key=lambda c: (nice(c), abs(c - x))):.2f}"
    v = float(re.sub(r"(st|ct|oct|/16)$", "", s))
    return str(int(v)) if is_int else f"{v:.2f}"


def block(p, sec, keys, skip=()):
    vals = dict(p.section(sec))
    out = []
    for key, name, kind in keys:
        if "." in key:
            s2, k2 = key.split(".")
            v = p.get(s2, k2)
            d = DEFAULT.get((s2, k2))
        else:
            v = vals.get(key)
            d = DEFAULT.get((sec, key))
        if v is None or key in skip or (d is not None and num(v) == num(d)):
            continue
        out.append(f"{name}:{fmt_unit(kind, v)}")
    known = {k for k, _, _ in keys} | set(skip)
    for key, v in vals.items():
        if key in known or (sec, key) in DROPPED or key == "Model":
            continue
        d = DEFAULT.get((sec, key))
        if d is None or v != d:
            out.append(f"{key.lower()}:{v}")
    return out


def body(items):
    return "{" + " ".join(items) + "}" if items else "_"


def meta_line(s):
    s = s.replace("\\r\\n", " / ").replace("\\'", "'").replace("\n", " ")
    return s.strip() or "_"


def silence_lfo(p, sec):
    p.set(sec, "DMS1", "0")
    p.set(sec, "DMD1", "100.00")


def live_slots(p):
    return [r for r in ROUTES if int(p.get(r[0], r[1])) != 0 and float(p.get(r[0], r[2])) != 0]


def to_idc(raw: bytes, name: str, off=()) -> str:
    p = h2p.parse(raw)
    m = dict(p.meta)
    tags = " ".join(x.strip().replace(" ", "_") for k in ("Features", "Character") for x in m.get(k, "").split(",") if x.strip())
    lines = ["---", f"@preset {name}", f"@author {meta_line(m.get('Author', ''))}", f"@category {meta_line(m.get('Categories', ''))}",
             f"@tags {tags or '_'}", f"@comments {meta_line(m.get('Description', ''))}", f"@usage {meta_line(m.get('Usage', ''))}"]
    vers = next((h[6:] for h in p.head if h.startswith("#Vers=")), "10001")
    if vers != "10001":
        lines.append(f"@version {vers}")
    lines.append("")
    for bname, sec, keys in BLOCKS:
        if bname in off:
            lines.append(f"{bname} off _")
        elif bname in ("env1", "env2"):
            n = bname[-1]
            items = block(p, sec, [("Trig", "trigger", f"e:trig{n}")] + ENVKEYS)
            lines.append(f"{bname} {fmt_unit('e:envmodel', p.get(sec, 'Model'))} {body(items)}")
        elif bname in ("lfo1", "lfo2"):
            sync, rate = int(p.get(sec, "Sync")), num(p.get(sec, "Rate"))
            head = [f"sync:{SYNC[sync]}"]
            if sync in FREE:
                head.append(f"rate:{FREE[sync] * 2 ** rate:.4g}Hz")
            elif rate != 0:
                head.append(f"rate:{trim(rate)}oct")
            head += [f"depth_src:{fmt_unit('src', p.get(sec, 'DMS1'))}", f"depth_amount:{fmt_unit('f', p.get(sec, 'DMD1'))}"]
            lines.append(f"{bname} {body(head + block(p, sec, LFOKEYS, skip=('Sync', 'Rate', 'FMS1', 'FMD1', 'DMS1', 'DMD1')))}")
        elif bname == "arp":
            on = p.get("ARP", "OnOff") == "1"
            items = block(p, "ARP", ARPKEYS, skip=("OnOff",)) if on else []
            lines.append(f"arp {'on' if on else 'off'} {body(items)}")
        elif bname == "osc":
            lines.append(f"osc {fmt_unit('e:oscmodel', p.get(sec, 'Model'))} {body(block(p, sec, keys, skip=tuple(r[1] for r in ROUTES if r[0] == sec) + tuple(r[2] for r in ROUTES if r[0] == sec)))}")
        elif bname in ("hpf", "filter"):
            model = fmt_unit("e:hpfmodel" if bname == "hpf" else "e:vcfmodel", p.get(sec, "Model"))
            skip = tuple(r[1] for r in ROUTES if r[0] == sec) + tuple(r[2] for r in ROUTES if r[0] == sec)
            lines.append(f"{bname} {model} {body(block(p, sec, keys, skip=skip))}")
        else:
            skip = tuple(r[1] for r in ROUTES if r[0] == sec) + tuple(r[2] for r in ROUTES if r[0] == sec)
            lines.append(f"{bname} {body(block(p, sec, keys, skip=skip))}")
    lines.append("")
    fx = []
    for slot in (1, 2):
        module = p.get(f"FX{slot}", "Module").strip("'")
        kind = module[:-1]
        if p.get("main", f"#FX{slot}") == "1":
            fx.append(kind.lower())
            lines.append(f"fx{slot} {kind.lower()} on {body(block(p, f'{FXSEC[kind]}{slot}', FXKEYS[kind]))}")
        else:
            lines.append(f"fx{slot} off _")
    if len(fx) == 2:
        lines.insert(len(lines) - 2, f"effect_chain {fx[0]} > {fx[1]}")
    cc = p.get("main", "CcOp")
    lines.append(f"output {body([] if cc == DEFAULT[('main', 'CcOp')] else ['volume:' + fmt_unit('db20', cc)])}")
    lines.append("")
    lines.append("modulations")
    n = 0
    for sec, sk, dk, target, kind in live_slots(p):
        src = int(p.get(sec, sk))
        n += 1
        lines.append(f"[{n}] {SRC[src] if src < len(SRC) else '#' + str(src)} -> {{{target}:{fmt_unit(kind, p.get(sec, dk))}}}")
    return "\n".join(lines) + "\n"


TOK = re.compile(r"(\w+):([^\s{}]+)")


SKIPPED = []


def from_idc(text: str, strict: bool = True) -> bytes:
    SKIPPED.clear()
    p = h2p.parse((HERE / "init.h2p").read_bytes())
    lines = [l for l in text.splitlines() if l.strip() and l.strip() != "---" and not l.lstrip().startswith(("//", "#"))]
    meta = {}
    by_name = {b[0]: b for b in BLOCKS}
    in_mod = False
    fx_kind = {}

    def put(sec, keys, items):
        idx = {n: (k, kind) for k, n, kind in keys}
        for name, val in items:
            if name in idx:
                key, kind = idx[name]
                s2, k2 = key.split(".") if "." in key else (sec, key)
                like = DEFAULT.get((s2, k2), "0.00")
                try:
                    p.set(s2, k2, parse_unit(kind, val, like))
                except (ValueError, KeyError, StopIteration, IndexError):
                    if strict:
                        raise
                    SKIPPED.append(f"{sec}.{name}:{val}")
            else:
                kv = p.section(sec)
                key = next((k for k, _ in kv if k.lower() == name), None)
                if key is None:
                    if strict:
                        raise KeyError((sec, name))
                    SKIPPED.append(f"{sec}.{name}:{val}")
                    continue
                p.set(sec, key, val)

    for line in lines:
        if line.startswith("@"):
            k, _, v = line[1:].partition(" ")
            meta[k] = v
            continue
        if line == "modulations":
            in_mod = True
            for sec, sk, dk, _, _ in ROUTES:
                p.set(sec, sk, "0")
            continue
        if in_mod:
            try:
                m = re.match(r"\[\d+\]\s+(\S+)\s+->\s+\{(\w+):(\S+)\}$", line)
                src, target, val = m.groups()
                sec, sk, dk, _, kind = next(r for r in ROUTES if r[3] == target)
                p.set(sec, sk, str(int(src[1:]) if src.startswith("#") else SRC.index(src)))
                p.set(sec, dk, parse_unit(kind, val, DEFAULT[(sec, dk)]))
            except (AttributeError, StopIteration, ValueError, KeyError):
                if strict:
                    raise
                SKIPPED.append(line)
            continue
        head, _, rest = line.partition(" ")
        items = TOK.findall(rest)
        if head == "effect_chain":
            continue
        if rest == "off _" and head in ("env1", "env2", "lfo1", "lfo2", "hpf"):
            if head.startswith("lfo"):
                silence_lfo(p, by_name[head][1])
            continue
        if head in ("fx1", "fx2"):
            slot = head[-1]
            if rest == "off _":
                p.set("main", f"#FX{slot}", "0")
                continue
            kind, state = rest.split()[:2]
            name = kind.capitalize()
            if name not in FXSEC and not strict:
                SKIPPED.append(line)
                p.set("main", f"#FX{slot}", "0")
                continue
            p.set(f"FX{slot}", "Module", f"'{name}{slot}'")
            p.set("main", f"#FX{slot}", "1" if state == "on" else "0")
            put(f"{FXSEC[name]}{slot}", FXKEYS[name], items)
            continue
        if head == "output":
            for name, val in items:
                p.set("main", "CcOp", parse_unit("db20", val, "0.00"))
            continue
        if head in ("env1", "env2"):
            sec = by_name[head][1]
            p.set(sec, "Model", str(E["envmodel"].index(rest.split()[0])))
            put(sec, [("Trig", "trigger", f"e:trig{head[-1]}")] + ENVKEYS, items)
            continue
        if head in ("lfo1", "lfo2"):
            sec = by_name[head][1]
            d = dict(items)
            sname = d.pop("sync")
            sync = next(k for k, v in SYNC.items() if v == sname)
            p.set(sec, "Sync", str(sync))
            r = d.pop("rate", None)
            if r is None:
                p.set(sec, "Rate", "0.00")
            elif r.endswith("Hz"):
                hzv = float(r[:-2])
                x = math.log2(hzv / FREE[sync])
                best = min((round(x + dd * 0.01, 2) for dd in range(-5, 6)),
                           key=lambda c: (f"{FREE[sync] * 2 ** c:.4g}Hz" != r, abs(c - x)))
                p.set(sec, "Rate", f"{best:.2f}")
            else:
                p.set(sec, "Rate", f"{float(r[:-3]):.2f}")
            put(sec, LFOKEYS, list(d.items()))
            continue
        if head == "arp":
            p.set("ARP", "OnOff", "1" if rest.split()[0] == "on" else "0")
            put("ARP", ARPKEYS, items)
            continue
        if head in ("osc", "hpf", "filter"):
            sec, keys = by_name[head][1], by_name[head][2]
            enum = {"osc": "oscmodel", "hpf": "hpfmodel", "filter": "vcfmodel"}[head]
            p.set(sec, "Model", str(E[enum].index(rest.split()[0])))
            put(sec, keys, items)
            continue
        if head in by_name:
            put(by_name[head][1], by_name[head][2], items)
            continue
        if strict:
            raise ValueError(f"unknown line: {line}")
        SKIPPED.append(line)
    unq = lambda s: "" if s == "_" else s
    p.meta = [("Bank", "deaftones"), ("Author", unq(meta.get("author", "")).replace("'", "\\'"))]
    if unq(meta.get("comments", "")):
        p.meta.append(("Description", unq(meta["comments"]).replace("'", "\\'")))
    if unq(meta.get("usage", "")):
        p.meta.append(("Usage", unq(meta["usage"]).replace(" / ", "\\r\\n").replace("'", "\\'")))
    p.meta.append(("Categories", unq(meta.get("category", ""))))
    tags = [t.replace("_", " ") for t in unq(meta.get("tags", "")).split()]
    p.meta.append(("Features", ", ".join(tags)))
    if "version" in meta:
        p.head = [f"#Vers={meta['version']}" if h.startswith("#Vers=") else h for h in p.head]
    return h2p.write(p)
