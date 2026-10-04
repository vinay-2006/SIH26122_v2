#!/usr/bin/env python3
"""Export the SAME all-MiniLM-L6-v2 network that sentence-transformers uses to ONNX (the Transformer module only: pooling and normalisation are done by
backend/v2/matching/embedder.py exactly as sentence-transformers does them), and copy its tokenizer.json next to it.

  python3 scripts/export_minilm_onnx.py [OUT_DIR]        default OUT_DIR: .local/models/minilm-onnx   (git-ignored)

Also writes MANIFEST.json beside the model: the source model revision and weights hash, the sha256 of every file, the export toolchain versions and a few probe
vectors. The runtime (backend/v2/matching/embedder.py) refuses a model whose files do not match the manifest; scripts/verify_onnx_model.py re-checks both the hashes
and the probe vectors. The bytes of model.onnx depend on the exporting toolchain (identical across runs of one environment, different across torch/onnx versions), so
the canonical build uses the versions pinned in requirements-export.txt.

Needs torch + sentence-transformers (the development machine / the build step), never the serverless runtime."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PROBES = ["Hydrotest of line A3 section 2 completed", "NRE-4050 piping 10 percent", "Mainline welding spread 1 crossing the railway"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export(out: Path) -> Path:
    import torch
    from sentence_transformers import SentenceTransformer
    st = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    net = st[0].auto_model.eval()
    tok = st.tokenizer
    out.mkdir(parents=True, exist_ok=True)
    enc = tok(["export probe", "a second, longer probe sentence for the dynamic axes"], padding=True, return_tensors="pt")
    args = (enc["input_ids"], enc["attention_mask"], enc["token_type_ids"])
    kw = dict(input_names=["input_ids", "attention_mask", "token_type_ids"], output_names=["last_hidden_state"], opset_version=17,
              dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"}, "token_type_ids": {0: "batch", 1: "seq"},
                            "last_hidden_state": {0: "batch", 1: "seq"}})
    with torch.no_grad():
        try:
            torch.onnx.export(net, args, str(out / "model.onnx"), dynamo=False, **kw)
        except TypeError:                                              # older torch has no dynamo switch
            torch.onnx.export(net, args, str(out / "model.onnx"), **kw)
    import onnx                                                        # one self-contained file (the exporter may write the weights to a side file)
    m = onnx.load(str(out / "model.onnx"), load_external_data=True)
    onnx.save_model(m, str(out / "model.onnx"), save_as_external_data=False)
    (out / "model.onnx.data").unlink(missing_ok=True)
    tok.save_pretrained(str(out))                                      # writes tokenizer.json (+ vocab, config)
    for extra in ("vocab.txt", "tokenizer_config.json", "special_tokens_map.json"):
        (out / extra).unlink(missing_ok=True)
    if not (out / "tokenizer.json").is_file():
        raise SystemExit("tokenizer.json was not produced")
    write_manifest(out, st)
    return out


def write_manifest(out: Path, st) -> None:
    import onnx
    import onnxscript
    import sentence_transformers
    import torch
    import transformers
    from huggingface_hub import snapshot_download
    snap = Path(snapshot_download("sentence-transformers/all-MiniLM-L6-v2", local_files_only=True))
    probes = st.encode(PROBES, normalize_embeddings=True, convert_to_numpy=True)
    manifest = {
        "model": "sentence-transformers/all-MiniLM-L6-v2", "revision": snap.name, "source_weights_sha256": sha256(snap / "model.safetensors"),
        "max_seq_length": 256, "embedding_dim": 384,
        "files": {n: {"sha256": sha256(out / n), "bytes": (out / n).stat().st_size} for n in ("model.onnx", "tokenizer.json")},
        "toolchain": {"torch": torch.__version__, "onnx": onnx.__version__, "onnxscript": onnxscript.__version__, "transformers": transformers.__version__,
                      "sentence_transformers": sentence_transformers.__version__, "python": sys.version.split()[0]},
        "probe": {"texts": PROBES, "vectors": [[round(float(x), 6) for x in v] for v in probes], "tolerance": 1e-5},
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / ".local" / "models" / "minilm-onnx"
    d = export(dest)
    print(f"wrote {d}/model.onnx ({(d / 'model.onnx').stat().st_size / 1e6:.1f} MB) and tokenizer.json")
