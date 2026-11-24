"""Deterministic names for LFO shapes, wavetables and blobs.

The .idc refers to an LFO shape, a synthesized wavetable or a blob (wave data, audio file, sample) by
name; the restorer gets the content back from a map. A name here is a pure function of the content:
words from a 62-word list picked by a hash of the content mixed with a seed. Same content, same name,
in any batch.

The seed was brute-forced per group (one kind and one base name, e.g. LFO shapes first named
"Triangle") over every preset of the training set, so no two different contents in a group share a
name. The most used content of each LFO / wavetable group keeps the plain base name (the stock Vital
shape is the most used "Triangle"). The seeds are in vital_names.json.

The mixer is integer-only (splitmix64), so the names are the same on any machine.
"""
import hashlib

# the 62 words of vitals_to_jsonl_to_idc_v3's BASE_WORDS, in its order
WORDS = [
    "silk", "rust", "bone", "crisp", "nitro",
    "haze", "veil", "edge", "peak", "tide", "pulse", "drift",
    "root", "fold", "press",
    "burn", "carve", "forge", "trace", "mark", "bind", "draw", "pour", "rise",
    "sharp", "hollow", "bright", "heavy", "light", "still", "swift", "rough",
    "narrow", "fierce", "obsidian",
    "tallow", "resin", "flint", "chalk", "loom", "husk", "burl", "tine", "weft", "clay",
    "jinx", "nexus", "keel", "pixel", "umbra", "zinc", "etched", "quartz", "joule", "fulcrum",
    "ibex", "moss", "patina", "stochastic", "sentinel", "dodecahedron", "abysmal",
]
M64 = (1 << 64) - 1
M63 = (1 << 63) - 1
C1, C2, GOLD = 0xBF58476D1CE4E5B9, 0x94D049BB133111EB, 0x9E3779B97F4A7C15


def content_hash(content: str) -> int:
    """64-bit hash of a canonical content string."""
    return int.from_bytes(hashlib.sha256(content.encode("utf-8")).digest()[:8], "little")


def mix(h: int, seed: int) -> int:
    """splitmix64 of (h + seed * golden), as an unsigned 64-bit int."""
    z = (h + seed * GOLD) & M64
    z = ((z ^ (z >> 30)) * C1) & M64
    z = ((z ^ (z >> 27)) * C2) & M64
    return z ^ (z >> 31)


def index(h: int, seed: int, k: int) -> int:
    return (mix(h, seed) & M63) % (len(WORDS) ** k)


def words(i: int, k: int) -> str:
    out = []
    for _ in range(k):
        i, r = divmod(i, len(WORDS))
        out.append(WORDS[r])
    return "_".join(out)


_GROUPS = None


def base_of(name: str, content: str, kind: str) -> str:
    """The base a content-derived name was made from, or the name itself when it is plain or unknown."""
    global _GROUPS
    if _GROUPS is None:
        import json, os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vital_names.json")
        _GROUPS = json.load(open(p, encoding="utf-8"))["groups"] if os.path.exists(p) else {}
    parts = name.split("_")
    for cut in range(1, min(7, len(parts))):
        base = "_".join(parts[:-cut])
        g = _GROUPS.get(kind, {}).get(base)
        if g and name_for(base, content, g) == name:
            return base
    return name


def name_for(base: str, content: str, group: dict) -> str:
    """group: {"k": words, "seed": seed, "plain": content hash hex or None}."""
    h = content_hash(content)
    if group.get("plain") is not None and f"{h:016x}" == group["plain"]:
        return base
    return f"{base}_{words(index(h, group['seed'], group['k']), group['k'])}"


# ---------------------------------------------------------------- GPU search

def _signed(c):
    return c - (1 << 64) if c >= (1 << 63) else c


def search(hashes, k, max_seeds=1 << 26, batch=None, device=None):
    """First seed under which the given content hashes all get different k-word
    names, or None within max_seeds. Uses torch (GPU when there is one)."""
    import torch
    n = len(hashes)
    space = len(WORDS) ** k
    if n > space:
        return None
    if n <= 1:
        return 0
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    if batch is None:
        batch = max(1, min(1 << 16, (1 << 26) // n))
    H = torch.tensor([_signed(h) for h in hashes], dtype=torch.int64, device=device)
    c1, c2, gold = (torch.tensor(_signed(c), dtype=torch.int64, device=device) for c in (C1, C2, GOLD))
    m63 = torch.tensor(M63, dtype=torch.int64, device=device)

    def lsr(z, s):  # logical shift right on int64
        return (z >> s) & ((1 << (64 - s)) - 1)

    start = 0
    while start < max_seeds:
        seeds = torch.arange(start, min(start + batch, max_seeds), dtype=torch.int64, device=device)
        z = H[None, :] + seeds[:, None] * gold
        z = (z ^ lsr(z, 30)) * c1
        z = (z ^ lsr(z, 27)) * c2
        z = z ^ lsr(z, 31)
        idx = torch.remainder(z & m63, space)
        s, _ = torch.sort(idx, dim=1)
        ok = ~(s[:, 1:] == s[:, :-1]).any(dim=1)
        hit = torch.nonzero(ok)
        if len(hit):
            seed = int(seeds[hit[0, 0]])
            got = {index(h, seed, k) for h in hashes}  # same names in Python
            assert len(got) == n, "GPU and Python mixers disagree"
            return seed
        start += len(seeds)
    return None
