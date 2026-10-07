#!/usr/bin/env python3
"""Measure what the serverless (ONNX) deployment costs at run time: import time, model load time, peak memory, first-index and per-query latency.
torch / faiss / sentence_transformers / pytesseract are blocked, as in the slim bundle.   usage: V2_ONNX_MODEL_DIR=... python3 scripts/measure_onnx_runtime.py"""
import importlib.abc
import os
import resource
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("torch", "faiss", "sentence_transformers", "pytesseract"):
            raise ImportError("blocked: " + name)


sys.meta_path.insert(0, Block())
os.environ["V2_EMBEDDING_BACKEND"] = "onnx"
os.environ.setdefault("V2_ONNX_MODEL_DIR", os.path.join(os.path.dirname(__file__), "..", "backend", "v2", "matching", "onnx_model"))
os.environ.setdefault("V2_ONNX_THREADS", "1")                       # a Vercel function has 1 vCPU
rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)     # MB
t = time.perf_counter()
from backend.v2.matching import embedder, index                       # noqa: E402
from backend.v2.seed.projects_spec import all_projects                 # noqa: E402
import backend.v2.app                                                  # noqa: E402,F401  (the whole API, as the function loads it)
print(f"import of the API: {time.perf_counter() - t:.2f}s, peak RSS {rss():.0f} MB; torch loaded: {'torch' in sys.modules}")
from pathlib import Path
t = time.perf_counter(); embedder.verify_files(Path(os.environ["V2_ONNX_MODEL_DIR"])); print(f"integrity check (sha256 of the 91 MB model): {time.perf_counter() - t:.2f}s")
t = time.perf_counter(); embedder.encode(["warm up"]); print(f"model load + first encode: {time.perf_counter() - t:.2f}s, peak RSS {rss():.0f} MB")
for p in all_projects():
    rows = [dict(activity_id=a.id, activity_name=a.name, wbs_code=" / ".join(a.wbs), discipline=a.discipline, location=a.location or None, asset_tag=None) for a in p.acts]
    t = time.perf_counter(); index.search(p.code, "v", rows, "welding of the pipeline", 3); first = time.perf_counter() - t
    t = time.perf_counter()
    for _ in range(20):
        index.search(p.code, "v", rows, "hydrotest of line section", 3)
    print(f"{p.code:14s} {len(rows):3d} activities: first claim (index build + query) {first * 1000:6.0f} ms; later claims {(time.perf_counter() - t) / 20 * 1000:5.1f} ms each")
for n in (500, 2000, 5000):                                              # far larger than any demo schedule: does an index build stay inside a function's limits?
    rows = [dict(activity_id=f"X{i:05d}", activity_name=f"Mainline welding spread {i % 9} section {i}", wbs_code=f"1.{i % 7}.{i}", discipline="Piping", location=f"Unit {i % 12}", asset_tag=None) for i in range(n)]
    t = time.perf_counter(); index.search(f"big{n}", "v", rows, "welding of the pipeline", 3); print(f"{n:5d} activities: first claim (index build + query) {time.perf_counter() - t:6.1f} s, peak RSS {rss():.0f} MB")
print(f"final peak RSS {rss():.0f} MB")
