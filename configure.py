"""One run that sets everything up and writes settings.json. Every question has a default in brackets:
Enter takes it, a path replaces it, "-" skips that part.

    python configure.py [--yes] [--gguf FILE] [--quant Q4_K_M|Q8_0] [--llama-server EXE] [--hf]

1. checks the Python packages;
2. the model: the GGUF from Hugging Face (or one you already have) and its tokenizer;
3. llama.cpp's llama-server: one you have, or the latest Windows Vulkan build from GitHub;
4. the synths' VST3 plugins, for rendering (a synth without one still gets its preset file);
5. the synths' factory content (wavetables, samples, LFO shapes) the presets refer to: the content
   pack from Hugging Face, plus your Serum 2 install for its table paths.

--yes takes every default without asking. --hf also fetches the bnb-nf4 checkpoint for
serve.py --backend hf (needs torch, bitsandbytes and a GPU with 9 GB).
"""
import argparse, importlib.util, json, os, shutil, subprocess, sys, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import settings  # noqa: E402

REPO = "IDONTCARELETMEIN/deaftones-8B"
GGUF_REPO = "IDONTCARELETMEIN/deaftones-8B-GGUF"
NF4_REPO = "IDONTCARELETMEIN/deaftones-8B-bnb-nf4"
CONTENT_REPO = "IDONTCARELETMEIN/deaftones-content"
VST3 = Path(os.environ.get("CommonProgramFiles", r"C:\Program Files\Common Files")) / "VST3"
PLUGINS = {"serum2": ["Serum2.vst3"], "vital": ["Vital.vst3"], "diva": ["Diva(x64).vst3", "Diva.vst3"]}
DOCS = Path.home() / "Documents"
CONTENT = {"vital": [DOCS / "Vital"], "serum2": [DOCS / "Xfer" / "Serum 2 Presets"]}
PACKAGES = {"numpy": "numpy", "soundfile": "soundfile", "transformers": "transformers",
            "huggingface_hub": "huggingface_hub", "cbor2": "cbor2", "zstandard": "zstandard",
            "dawdreamer": "dawdreamer"}
A = None


def ask(question, default):
    if A.yes:
        print(f"{question} [{default}]")
        return default
    got = input(f"{question} [{default}] ").strip().strip('"')
    return default if not got else got


def step(title):
    print(f"\n== {title}")


def packages():
    step("Python packages")
    missing = [pip for mod, pip in PACKAGES.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        print("all there")
        return True
    print("missing: " + ", ".join(missing))
    if ask("install them with pip now? (y/n)", "y").lower().startswith("y"):
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=False)
    still = [pip for mod, pip in PACKAGES.items() if importlib.util.find_spec(mod) is None]
    if still:
        print("still missing: " + ", ".join(still) + ("  (without dawdreamer nothing is rendered)" if still == ["dawdreamer"] else ""))
    return "huggingface_hub" not in still and "transformers" not in still


def model(s):
    from huggingface_hub import hf_hub_download
    step("Model")
    models = Path(s["models"])
    name = f"deaftones-8B-{A.quant}.gguf"
    have = A.gguf or s["gguf"] or str(models / name)
    path = ask("GGUF file (downloaded there if missing)", have)
    if path != "-":
        if not Path(path).exists():
            print(f"downloading {name} ({'5.0' if A.quant == 'Q4_K_M' else '8.7'} GB) ...")
            path = hf_hub_download(GGUF_REPO, name, local_dir=str(Path(path).parent))
        s["gguf"] = str(Path(path).resolve())
        print(f"model: {s['gguf']}")
    tok = models / "tokenizer"
    for f in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja"):
        if not (tok / f).exists():
            hf_hub_download(REPO, f, local_dir=str(tok))
    s["tokenizer"] = str(tok)
    print(f"tokenizer: {tok}")
    if A.hf:
        from huggingface_hub import snapshot_download
        print("downloading the bnb-nf4 checkpoint (7.3 GB) for --backend hf ...")
        s["hf_model"] = snapshot_download(NF4_REPO, local_dir=str(models / "deaftones-8B-bnb-nf4"))


def llama(s):
    step("llama.cpp")
    found = A.llama_server or s["llama_server"] or shutil.which("llama-server") or ""
    path = ask("llama-server.exe (Enter on 'download' fetches the latest Windows Vulkan build)", found or "download")
    if path == "download":
        releases = json.load(urllib.request.urlopen("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30", timeout=60))
        asset = next(a for r in releases for a in r["assets"] if a["name"].endswith("-bin-win-vulkan-x64.zip"))
        dst = ROOT / "tools" / "llama.cpp"
        dst.mkdir(parents=True, exist_ok=True)
        z = dst / asset["name"]
        print(f"downloading {asset['name']} ({asset['size'] / 2**20:.0f} MB) ...")
        urllib.request.urlretrieve(asset["browser_download_url"], z)
        zipfile.ZipFile(z).extractall(dst)
        z.unlink()
        path = str(next(dst.rglob("llama-server.exe")))
    if path != "-":
        if not Path(path).exists():
            print(f"not found: {path}")
            return
        s["llama_server"] = str(Path(path).resolve())
        print(f"llama-server: {s['llama_server']}")


def plugins(s):
    step("Synth plugins (VST3), for rendering")
    for synth, names in PLUGINS.items():
        found = s["vst"].get(synth) or next((str(VST3 / n) for n in names if (VST3 / n).exists()), "")
        path = ask(f"{synth} plugin ('-' for none)", found or "-")
        s["vst"][synth] = "" if path == "-" or not Path(path).exists() else str(Path(path))
        print(f"{synth}: {s['vst'][synth] or 'none, presets are written but not rendered'}")


def content(s):
    step("Factory content (wavetables, samples, LFO shapes)")
    env = settings.env(s)
    pack = False
    if ask("download the content pack from Hugging Face, 240 MB (y/n)", "y").lower().startswith("y"):
        from huggingface_hub import hf_hub_download
        z = hf_hub_download(CONTENT_REPO, "deaftones-content.zip", repo_type="dataset", local_dir=s["models"])
        zipfile.ZipFile(z).extractall(s["cache"])
        pack = True
        print(f"unpacked into {s['cache']}")
    for synth in ("vital", "serum2"):
        have = s["content"].get(synth) or [str(p) for p in CONTENT[synth] if p.exists()]
        if synth == "vital" and pack:
            have = []
        what = "its install, for the table paths" if synth == "serum2" else "folders to add to the pack" if pack else "folders to read"
        got = ask(f"{synth}: {what}, ';' between them ('-' to skip)", ";".join(have) or "-")
        folders = [] if got == "-" else [f for f in got.split(";") if Path(f).exists()]
        s["content"][synth] = folders
        if not folders:
            print(f"{synth}: " + ("from the pack" if pack else "none; its presets fall back to the nearest table they can find"))
            continue
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "converters" / synth / "extract.py"), *folders],
                       env=env, check=False)


def main():
    global A
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--gguf")
    ap.add_argument("--quant", choices=["Q4_K_M", "Q8_0"], default="Q4_K_M")
    ap.add_argument("--llama-server", dest="llama_server")
    ap.add_argument("--hf", action="store_true")
    A = ap.parse_args()
    s = settings.load()
    for k in ("models", "cache", "out"):
        Path(s[k]).mkdir(parents=True, exist_ok=True)
    if not packages():
        print("\ntransformers and huggingface_hub are needed to go on: pip install -r requirements.txt")
        return
    model(s)
    settings.save(s)
    llama(s)
    settings.save(s)
    plugins(s)
    settings.save(s)
    content(s)
    settings.save(s)
    step("Done")
    print(f"settings: {settings.FILE}\nstart it: python serve.py   then open http://127.0.0.1:8774")


if __name__ == "__main__":
    main()
