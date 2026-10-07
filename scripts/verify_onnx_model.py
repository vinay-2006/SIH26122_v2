#!/usr/bin/env python3
"""Check an ONNX model directory against its MANIFEST.json: file hashes, then the probe vectors (the toolchain-independent proof that it is still the MiniLM network).
   python3 scripts/verify_onnx_model.py [DIR]        default DIR: backend/v2/matching/onnx_model        exit 0 = verified
   --reference-manifest PATH   additionally require the same file hashes as another manifest (e.g. the committed one) for a model built elsewhere"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv) -> int:
    args = [a for a in argv if not a.startswith("--")]
    ref = argv[argv.index("--reference-manifest") + 1] if "--reference-manifest" in argv else None
    d = Path(args[0] if args and args[0] != ref else ROOT / "backend" / "v2" / "matching" / "onnx_model")
    from backend.v2.matching.embedder import EmbeddingBackendError, OnnxEmbedder, verify_files
    try:
        manifest = verify_files(d)
        print(f"hashes OK: " + ", ".join(f"{n} {v['sha256'][:12]}… ({v['bytes'] / 1e6:.1f} MB)" for n, v in manifest["files"].items()))
        if ref:
            other = json.loads(Path(ref).read_text())["files"]
            for n, v in manifest["files"].items():
                if other[n]["sha256"] != v["sha256"]:
                    raise EmbeddingBackendError(f"{n} differs from the reference manifest")
            print("same file hashes as the reference manifest")
        import numpy as np
        emb = OnnxEmbedder(d).encode(manifest["probe"]["texts"])
        dev = float(np.abs(emb - np.array(manifest["probe"]["vectors"], dtype=np.float32)).max())
        print(f"probe vectors: max deviation {dev:.2e} (tolerance {manifest['probe']['tolerance']:g})")
        if dev > manifest["probe"]["tolerance"]:
            raise EmbeddingBackendError("probe vectors do not match: this is not the expected network")
    except EmbeddingBackendError as e:
        print("NOT VERIFIED:", e)
        return 1
    print("VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
