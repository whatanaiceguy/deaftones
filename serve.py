"""A page that takes a synth and a request, streams the model's answer, then converts it to a preset
and plays its render. Run configure.py first; every path comes from settings.json.

    python serve.py [--backend llama|hf] [--port 8774]

The default backend is llama.cpp: llama-server is started here with the GGUF and dies with this
process. --backend hf runs the bnb-nf4 checkpoint in transformers (engine/fastgen.py). Each answer is
kept in <out>/<time>_<synth>/: request.json, answer.txt, <name>.units.idc, the preset file and
<name>.wav.
"""
import argparse, json, mimetypes, os, re, signal, subprocess, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "engine"))
sys.path.insert(0, str(HERE))

import settings  # noqa: E402
from prompt import messages, chat_ids  # noqa: E402
import convert as C  # noqa: E402

S = settings.load()
ap = argparse.ArgumentParser()
ap.add_argument("--backend", choices=["llama", "hf"], default="llama")
ap.add_argument("--port", type=int, default=8774)
ap.add_argument("--out", default=S["out"])
ap.add_argument("--max_new", type=int, default=8192)
ap.add_argument("--gguf", default=S["gguf"])
ap.add_argument("--llama", default=S["llama_server"])
ap.add_argument("--llama_port", type=int, default=8782)
ap.add_argument("--model", default=S["hf_model"])
ap.add_argument("--tokenizer", default=S["tokenizer"])
A = ap.parse_args()
OUT = Path(A.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)

STATE = {"model": None, "tok": None, "busy": False, "stop": False, "load": "loading"}
LOCK = threading.Lock()


def load():
    from transformers import AutoTokenizer
    t = time.time()
    tok = AutoTokenizer.from_pretrained(A.tokenizer or A.model, use_fast=True)
    if A.backend == "llama":
        from llamagen import LlamaGen
        lg = LlamaGen(A.llama, A.gguf, port=A.llama_port, log=OUT / "llama-server.log")
        STATE["lg"] = lg
        lg.wait()
        STATE.update(model="llama", tok=tok, fg=lg, load=f"ready: {Path(A.gguf).name} on llama.cpp, loaded in {time.time() - t:.0f}s")
        print(STATE["load"], flush=True)
        return
    import torch
    from moss_music import MossMusicModel
    from fastgen import FastGen, unnest_4bit
    model = MossMusicModel.from_pretrained(A.model, dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa")
    model.eval()
    unnest_4bit(model)
    STATE.update(model=model, tok=tok, fg=FastGen(model, tok), load=f"ready: {Path(A.model).name}, {torch.cuda.memory_allocated() / 2**30:.1f} GiB, loaded in {time.time() - t:.0f}s")
    print(STATE["load"], flush=True)


def generate(synth, request, temperature, top_k, emit):
    tok, fg = STATE["tok"], STATE["fg"]
    ids = chat_ids(tok, messages(C.SYNTHS[synth], request))
    t = time.time()
    text = ""
    for piece, n in fg.stream(ids, max_new=A.max_new, temperature=temperature, top_k=top_k, stop=lambda: STATE["stop"]):
        text += piece
        emit({"t": piece, "n": n})
    return text.strip(), dict(tokens=len(fg.ids), seconds=round(time.time() - t, 1), ended=fg.ended, stopped=STATE["stop"])


def job(q, emit):
    synth = q["synth"]
    if synth not in C.SYNTHS:
        raise ValueError("synth")
    d = OUT / f"{time.strftime('%Y%m%d-%H%M%S')}_{synth}"
    d.mkdir(parents=True)
    temperature, top_k = float(q.get("temperature", 0.9)), int(q.get("top_k", 45))
    text, stats = generate(synth, q["request"], temperature, top_k, emit)
    meta = dict(synth=synth, request=q["request"], temperature=temperature, top_k=top_k,
                model=A.gguf if A.backend == "llama" else A.model, **stats)
    (d / "answer.txt").write_text(text, encoding="utf-8")
    (d / "request.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    emit({"stats": stats})
    if "</think>" not in text:
        emit({"done": entry(d)})
        return
    emit({"converting": True})
    try:
        name = C.answer(text)[2]
        res = C.convert(text, d / (re.sub(r"[^\w\- ]", "_", name)[:60].strip("_ ") or synth), synth)
        meta["result"] = {k: (Path(v).name if k in ("preset", "wav") and v else v) for k, v in res.items()}
    except Exception as e:
        traceback.print_exc()
        meta["error"] = f"{type(e).__name__}: {e}"
    (d / "request.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    emit({"done": entry(d)})


def entry(d):
    m = json.loads((d / "request.json").read_text(encoding="utf-8"))
    m["dir"] = d.name
    return m


def history():
    out = []
    for d in sorted(OUT.iterdir(), reverse=True):
        if (d / "request.json").exists():
            try:
                out.append(entry(d))
            except Exception:
                pass
    return out


def recycle(path):
    import ctypes
    from ctypes import wintypes

    class Op(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]
    op = Op(None, 3, str(path) + "\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not path.exists()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            return self.send(200, (HERE / "page.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if u.path == "/status":
            return self.send(200, {"load": STATE["load"], "busy": STATE["busy"]})
        if u.path == "/history":
            return self.send(200, history())
        if u.path == "/file":
            p = (OUT / q.get("p", "")).resolve()
            if OUT not in p.parents or not p.is_file():
                return self.send(404, {"error": "no file"})
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(p.stat().st_size))
            if q.get("dl"):
                self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
            self.end_headers()
            self.wfile.write(p.read_bytes())
            return
        if u.path == "/delete":
            p = (OUT / q.get("d", "")).resolve()
            if p.parent != OUT or not (p / "request.json").is_file():
                return self.send(404, {"error": "no such preset"})
            return self.send(200, {"ok": recycle(p)})
        if u.path == "/reveal":
            p = (OUT / q.get("p", "")).resolve()
            if OUT not in p.parents or not p.is_file():
                return self.send(404, {"error": "no file"})
            subprocess.Popen(f'explorer /select,"{p}"')
            return self.send(200, {"ok": True})
        if u.path == "/stop":
            STATE["stop"] = True
            return self.send(200, {"ok": True})
        if u.path == "/gen":
            if STATE["model"] is None:
                return self.send(503, {"error": STATE["load"]})
            if not LOCK.acquire(blocking=False):
                return self.send(409, {"error": "busy"})
            STATE.update(busy=True, stop=False)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(obj):
                try:
                    self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    pass
            try:
                job(q, emit)
            except Exception as e:
                traceback.print_exc()
                emit({"error": f"{type(e).__name__}: {e}"})
            finally:
                STATE["busy"] = False
                LOCK.release()
            return
        self.send(404, {"error": "no route"})


if __name__ == "__main__":
    def boot():
        need = Path(A.gguf or "-") if A.backend == "llama" else Path(A.model or "-") / "model.safetensors"
        if not need.exists():
            return STATE.update(load=f"no model at {need}: run configure.py")
        if A.backend == "llama" and not Path(A.llama or "-").exists():
            return STATE.update(load=f"no llama-server at {A.llama}: run configure.py")
        try:
            load()
        except Exception as e:
            traceback.print_exc()
            STATE.update(load=f"load failed: {type(e).__name__}: {e}")
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", signal.SIGINT)):
        signal.signal(sig, lambda *a: (STATE.get("lg") and STATE["lg"].stop(), os._exit(0)))
    threading.Thread(target=boot, daemon=True).start()
    print(f"http://127.0.0.1:{A.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", A.port), H).serve_forever()
