"""What the converter and the restorer must agree on.

osc_distortion_names: Vital's FM and RM distortion types 7, 8, 10, 11 are relative,
the first and the second of the other two oscillators; the .idc names the real one.

value: what the restorer fills in for a setting the .idc leaves out, so the
converter writes every setting that differs from it.
"""

DISTORTION_BASE = {0: "none", 1: "sync", 2: "formant", 3: "quantize", 4: "bend", 5: "squeeze",
                   6: "pulse", 9: "sample_fm", 12: "rm_sample"}

HARDCODED = {
    "bypass": 0.0,
    "mpe_enabled": 0.0,
    "pitch_wheel": 0.0,
    "view_spectrogram": 0.0,
    "voice_amplitude": 1.0,
    "osc_1_spectral_morph_phase": 0.5,
    "osc_2_spectral_morph_phase": 0.5,
    "osc_3_spectral_morph_phase": 0.5,
    "osc_1_midi_track": 1.0,
    "osc_2_midi_track": 1.0,
    "osc_3_midi_track": 1.0,
    "osc_1_velocity_track": 0.0,
    "osc_2_velocity_track": 0.0,
    "osc_3_velocity_track": 0.0,
    "osc_1_view_2d": 1.0,
    "osc_2_view_2d": 1.0,
    "osc_3_view_2d": 1.0,
    "osc_1_smooth_interpolation": 0.0,
    "osc_2_smooth_interpolation": 0.0,
    "osc_3_smooth_interpolation": 0.0,
}


def others(n):
    return [o for o in (1, 2, 3) if o != n]


def osc_distortion_names(n):
    a, b = others(n)
    return {**DISTORTION_BASE, 7: f"fm_osc{a}", 8: f"fm_osc{b}", 10: f"rm_osc{a}", 11: f"rm_osc{b}"}


def osc_distortion_types(n):
    return {v: k for k, v in osc_distortion_names(n).items()}


def fm_sources(settings):
    used = set()
    for j in (1, 2, 3):
        if settings.get(f"osc_{j}_on", 0.0) != 1.0:
            continue
        a, b = others(j)
        t = int(settings.get(f"osc_{j}_distortion_type", 0) or 0)
        if t in (7, 10):
            used.add(a)
        elif t in (8, 11):
            used.add(b)
        elif t in (9, 12):
            used.add("sample")
    return used


def value(key, defaults):
    return HARDCODED[key] if key in HARDCODED else defaults.get(key)
