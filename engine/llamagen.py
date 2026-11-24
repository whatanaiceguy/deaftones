"""The GGUF through llama.cpp's llama-server, started and owned by this process: the server is put in
a Windows job object that kills it when this process ends, however it ends. The prompt goes in as
token ids from our own chat template; sampling is top-k then temperature and nothing else.

    lg = LlamaGen(exe, gguf, port=8782)
    for piece, n in lg.stream(ids, max_new=8192, temperature=0.7, top_k=45): ...
"""
import ctypes, http.client, json, os, subprocess, sys, time
from ctypes import wintypes
from pathlib import Path


def _kill_with_parent(proc):
    if sys.platform != "win32":
        return None

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.LimitFlags = 0x2000
    k.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
    k.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(proc._handle)))
    return job


class LlamaGen:
    def __init__(self, exe, gguf, port=8782, ctx=8192, log=None):
        self.port, self.ctx, self.gguf = port, ctx, Path(gguf)
        out = open(log, "w", encoding="utf-8") if log else subprocess.DEVNULL
        self.proc = subprocess.Popen([str(exe), "-m", str(gguf), "-ngl", "99", "-c", str(ctx), "-np", "1", "-fa", "on",
                                      "--host", "127.0.0.1", "--port", str(port), "--no-webui"],
                                     stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     cwd=str(Path(exe).parent))
        self.job = _kill_with_parent(self.proc)
        self.ids, self.ended = [], False

    def wait(self, timeout=300):
        t = time.time()
        while time.time() - t < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.proc.returncode}")
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                c.request("GET", "/health")
                if c.getresponse().status == 200:
                    return time.time() - t
            except OSError:
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server did not come up")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()

    def stream(self, ids, max_new=8192, temperature=0.7, top_k=45, stop=lambda: False):
        self.ids, self.ended = [], False
        body = {"prompt": list(ids), "n_predict": max(1, min(max_new, self.ctx - len(ids))), "stream": True,
                "temperature": temperature, "top_k": top_k or 0, "top_p": 1.0, "min_p": 0.0, "typical_p": 1.0,
                "repeat_penalty": 1.0, "presence_penalty": 0.0, "frequency_penalty": 0.0, "dry_multiplier": 0.0,
                "xtc_probability": 0.0, "samplers": ["top_k", "temperature"], "return_tokens": True,
                "cache_prompt": False, "seed": int.from_bytes(os.urandom(4), "little") >> 1}
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=600)
        c.request("POST", "/completion", json.dumps(body), {"Content-Type": "application/json"})
        r = c.getresponse()
        if r.status != 200:
            raise RuntimeError(f"llama-server {r.status}: {r.read()[:300]!r}")
        try:
            for line in r:
                if not line.startswith(b"data: "):
                    continue
                ev = json.loads(line[6:])
                self.ids += ev.get("tokens") or []
                if ev.get("content"):
                    yield ev["content"], len(self.ids)
                if ev.get("stop"):
                    self.ended = ev.get("stop_type") == "eos" or bool(ev.get("stopped_eos"))
                    break
                if stop():
                    break
        finally:
            c.close()
