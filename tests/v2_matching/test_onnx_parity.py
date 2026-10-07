"""The ONNX embedding backend must be the SAME retrieval as torch + FAISS, established here (not assumed): embeddings, scores and the top-k ranking over the four demo
schedules and a few hundred claims; a missing dependency / model never switches backend; the engine runs with torch and faiss absent. No database."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")
pytest.importorskip("faiss")
pytest.importorskip("sentence_transformers")
pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")
import numpy as np  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "sample_data" / "test-cases" / "matching"
MAX_SCORE_DEVIATION = 1e-5                      # float32 noise is ~1e-7; anything near 1e-5 would be a real difference
TIE_EPS = 1e-5


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    sys.path.insert(0, str(REPO / "scripts"))
    from export_minilm_onnx import export
    return export(tmp_path_factory.mktemp("minilm-onnx"))


def _projects():
    from backend.v2.seed.projects_spec import all_projects
    return all_projects()


def _activity_rows(spec):
    return [dict(activity_id=a.id, activity_name=a.name, wbs_code=" / ".join(a.wbs) or None, discipline=a.discipline, location=a.location or None, asset_tag=None)
            for a in spec.acts]


def _claims(spec):
    out = []
    for a in spec.acts:
        words = a.name.split()
        out += [f"{a.name} completed today", f"{' '.join(words[:3]).lower()} {a.location}".strip(), f"{a.id} 20 percent done", f"{a.discipline} work on {words[-1]}"]
    out += [json.loads(p.read_text())["raw_claim_text"] for p in sorted(CASES.glob("*.json"))]
    for line in (REPO / "sample_data" / "daily_progress_2026-08-16.csv").read_text().splitlines()[1:]:
        cols = line.split(",")
        if len(cols) > 4:
            out.append(cols[4])
    return out


def _run(backend, model_dir, monkeypatch, tag, spec, queries):
    from backend.v2.matching import index
    monkeypatch.setenv("V2_EMBEDDING_BACKEND", backend)
    monkeypatch.setenv("V2_ONNX_MODEL_DIR", str(model_dir))
    index._cache.clear()
    rows = _activity_rows(spec)
    return [[(c.activity_id, c.score) for c in index.search(spec.code, f"{tag}-{spec.code}", rows, q, 3)] for q in queries]


def test_onnx_embeddings_match_torch_embeddings(model_dir, monkeypatch):
    from backend.v2.matching import embedder
    texts = [t for s in _projects() for t in (f"{a.id} {a.name} {a.discipline} {a.location}" for a in s.acts)] + ["x " * 600, "Σ non-ascii – dash", ""]
    monkeypatch.setenv("V2_ONNX_MODEL_DIR", str(model_dir))
    monkeypatch.setenv("V2_EMBEDDING_BACKEND", "onnx")
    onnx = embedder.encode(texts)
    monkeypatch.setenv("V2_EMBEDDING_BACKEND", "torch")
    torch_ = embedder.encode(texts)
    dev = np.abs(onnx - torch_)
    cos = (onnx * torch_).sum(1)
    print(f"\nEMBEDDINGS n={len(texts)} max|d|={dev.max():.2e} mean|d|={dev.mean():.2e} min cosine={cos.min():.8f}")
    assert dev.max() < 1e-5 and cos.min() > 0.99999


def test_topk_ranking_and_scores_match_across_all_demo_schedules(model_dir, monkeypatch):
    deviations, queries_total, rank_diff, ties = [], 0, [], 0
    for spec in _projects():
        qs = _claims(spec)
        a = _run("torch", model_dir, monkeypatch, "t", spec, qs)
        b = _run("onnx", model_dir, monkeypatch, "o", spec, qs)
        for q, ra, rb in zip(qs, a, b):
            queries_total += 1
            assert len(ra) == len(rb), q
            deviations += [abs(x[1] - y[1]) for x, y in zip(ra, rb)]
            ids_a, ids_b = [x[0] for x in ra], [x[0] for x in rb]
            if ids_a != ids_b:
                # an order difference is only acceptable between candidates whose scores are indistinguishable (a genuine tie)
                near = all(abs(x[1] - y[1]) < TIE_EPS for x, y in zip(ra, rb))
                if near:
                    ties += 1
                else:
                    rank_diff.append((spec.code, q, ra, rb))
    d = np.array(deviations)
    print(f"\nTOP-3 PARITY queries={queries_total} candidates={len(d)} max score dev={d.max():.2e} mean={d.mean():.2e} rank mismatches={len(rank_diff)} exact-tie reorderings={ties}")
    assert not rank_diff, rank_diff[:3]
    assert d.max() < MAX_SCORE_DEVIATION


def test_selection_is_explicit_and_never_silently_switches(model_dir, monkeypatch, tmp_path):
    from backend.shared import schedule_index
    from backend.v2.matching import embedder, index
    monkeypatch.setenv("V2_EMBEDDING_BACKEND", "tensorflow")
    with pytest.raises(embedder.EmbeddingBackendError):
        embedder.selected()
    # onnx selected, model files missing: an error, and the torch model is NEVER loaded as a replacement
    monkeypatch.setenv("V2_EMBEDDING_BACKEND", "onnx")
    monkeypatch.setenv("V2_ONNX_MODEL_DIR", str(tmp_path / "nothing-here"))
    monkeypatch.setattr(embedder, "_onnx", None)
    loaded = []
    monkeypatch.setattr(schedule_index, "_get_model", lambda: loaded.append(1))
    index._cache.clear()
    with pytest.raises(embedder.EmbeddingBackendError, match="missing"):
        index.search("p", "v", _activity_rows(_projects()[0]), "welding", 3)
    assert not loaded, "the torch model must not be loaded when the ONNX backend is selected"
    # the start-up warm-up reports the failure and does not raise
    index.warm_up()
    # onnxruntime absent: reported as a missing dependency
    monkeypatch.setenv("V2_ONNX_MODEL_DIR", str(model_dir))
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    monkeypatch.setattr(embedder, "_onnx", None)
    with pytest.raises(embedder.EmbeddingBackendError, match="dependency"):
        embedder.encode(["x"])


def test_the_engine_and_the_onnx_search_run_with_torch_and_faiss_absent(model_dir):
    """a fresh interpreter in which importing torch / faiss / sentence_transformers fails, as in the serverless bundle"""
    code = textwrap.dedent("""
        import sys, importlib.abc
        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('torch', 'faiss', 'sentence_transformers', 'pytesseract'):
                    raise ImportError('blocked in this test: ' + name)
        sys.meta_path.insert(0, Block())
        from backend.v2.matching import service, index
        m, ExecutionClaim, Elig = service._engine()
        rows = [dict(activity_id='A1', activity_name='Hydrotest of line A3', wbs_code=None, discipline='Piping', location='Unit 4', asset_tag=None),
                dict(activity_id='A2', activity_name='Pipeline welding spread 1', wbs_code=None, discipline='Piping', location=None, asset_tag=None)]
        r = index.search('p', 'v', rows, 'welding of the pipeline', 2)
        assert [c.activity_id for c in r][0] == 'A2', r
        assert 'torch' not in sys.modules and 'faiss' not in sys.modules
        print('OK')
    """)
    env = dict(os.environ, V2_EMBEDDING_BACKEND="onnx", V2_ONNX_MODEL_DIR=str(model_dir), PYTHONPATH=str(REPO))
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=REPO, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-800:] + r.stderr[-1500:]


def test_a_model_that_does_not_match_its_manifest_is_refused(model_dir, tmp_path):
    """integrity: flipped bytes, a swapped tokenizer, or no manifest at all -> EmbeddingBackendError, never a loaded model"""
    import shutil
    from backend.v2.matching import embedder
    d = tmp_path / "m"
    shutil.copytree(model_dir, d)
    embedder.OnnxEmbedder(d)                                                # intact: loads
    raw = bytearray((d / "model.onnx").read_bytes()); raw[len(raw) // 2] ^= 0xFF
    (d / "model.onnx").write_bytes(bytes(raw))
    with pytest.raises(embedder.EmbeddingBackendError, match="integrity check failed for model.onnx"):
        embedder.OnnxEmbedder(d)
    shutil.copy(model_dir / "model.onnx", d / "model.onnx")
    (d / "tokenizer.json").write_text((d / "tokenizer.json").read_text().replace("[PAD]", "[PAX]", 1))
    with pytest.raises(embedder.EmbeddingBackendError, match="tokenizer.json"):
        embedder.OnnxEmbedder(d)
    shutil.copy(model_dir / "tokenizer.json", d / "tokenizer.json")
    (d / "MANIFEST.json").unlink()
    with pytest.raises(embedder.EmbeddingBackendError, match="MANIFEST.json"):
        embedder.OnnxEmbedder(d)


def test_the_committed_model_directory_matches_the_committed_manifest():
    """when the deployment bundle's model.onnx is present it must be the exact file the committed manifest pins (skipped on a checkout without the 91 MB file)"""
    d = REPO / "backend" / "v2" / "matching" / "onnx_model"
    if not (d / "model.onnx").is_file():
        pytest.skip("model.onnx is not present in this checkout (it is git-ignored; build it with scripts/export_minilm_onnx.py)")
    from backend.v2.matching.embedder import verify_files
    assert verify_files(d)["model"] == "sentence-transformers/all-MiniLM-L6-v2"
