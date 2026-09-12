"""brain_kg extraction (KG v3) — GLiNER2 encoder relation extraction.

NEW module (there was never a stub — verified 2026-09-12). Built against the
REAL GLiNER2 API confirmed by smoke-test, not assumptions:
  extract_entities(text, labels) -> {'entities': {label: [span, ...]}}
  extract_relations(text, rels)  -> {'relation_extraction': {rel: [(head, tail), ...]}}
heads/tails are TEXT SPANS (need span->entity-id resolution downstream, deterministic-ER).

Model: fastino/gliner2-base-v1 (Apache-2.0, deberta-v3-base, CPU, 205M). Runs ONLY
at batch/build time — NEVER in the search hot path. Must run in the isolated venv
brain_kg/.venv-kg (torch), not the brain runtime.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Pin the model + cache inside the project for reproducibility (rule: pin revision).
MODEL_ID = os.getenv("OPEN_BRAIN_KG_GLINER_MODEL", "fastino/gliner2-base-v1")
os.environ.setdefault("HF_HOME", os.path.join(os.path.dirname(__file__), ".hf-cache"))

# Fixed schema (bounded, auditable). Derived from the real corpus vocabulary.
ENTITY_LABELS = [
    "migration", "ticket", "subsystem", "credential", "person", "repo",
    "environment", "tool", "file", "concept",
]
RELATION_LABELS = [
    "implements", "blocks", "resolves", "deploys", "depends-on",
    "owns", "part-of", "supersedes", "mentions",
]

_model = None


def _resolve_device() -> str:
    """Prefer the GPU (RTX 5090) — CPU inference thermal-throttles the host.
    OPEN_BRAIN_KG_DEVICE overrides ('cuda'|'cpu'). Falls back to cpu with a loud
    warning if CUDA torch isn't available (so it's never silent again)."""
    import os as _os
    forced = _os.getenv("OPEN_BRAIN_KG_DEVICE")
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
    except Exception:
        cuda_ok = False
    if forced:
        return forced
    if cuda_ok:
        return "cuda"
    import sys as _sys
    print("WARNING: CUDA torch not available — GLiNER2 falling back to CPU "
          "(this pins the CPU + thermal-throttles). Install CUDA torch for the GPU.",
          file=_sys.stderr)
    return "cpu"


def _load():
    """Load GLiNER2 once (process-wide) onto the GPU when available. Logs the
    device at load time so device placement is NEVER ambiguous (handoff root-cause)."""
    global _model
    if _model is None:
        import sys as _sys
        from gliner2 import GLiNER2  # lazy: non-extract paths don't need torch
        device = _resolve_device()
        _model = GLiNER2.from_pretrained(MODEL_ID)
        try:
            _model = _model.to(device)
        except Exception as exc:  # pragma: no cover
            print(f"WARNING: could not move GLiNER2 to {device}: {exc}; staying on default",
                  file=_sys.stderr)
            device = "cpu(default)"
        print(f"[brain_kg.extract] GLiNER2 {MODEL_ID} loaded on device={device}", file=_sys.stderr)
    return _model


@dataclass(frozen=True)
class Triple:
    head: str          # raw text span
    relation: str
    tail: str          # raw text span
    source_memory_id: int | None = None  # the memory whose body produced it


def extract(text: str, source_memory_id: int | None = None) -> list[Triple]:
    """Return typed (head, relation, tail) triples from one memory body.

    Deterministic given the pinned model + default threshold. Empty text -> [].
    """
    if not text or not text.strip():
        return []
    m = _load()
    out = m.extract_relations(text, RELATION_LABELS)
    rel_map = out.get("relation_extraction", {}) if isinstance(out, dict) else {}
    triples: list[Triple] = []
    for relation, pairs in rel_map.items():
        for pair in pairs or []:
            # GLiNER2 returns 2-tuples (head, tail); guard shape defensively.
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            head, tail = pair
            head, tail = str(head).strip(), str(tail).strip()
            if head and tail:
                triples.append(Triple(head=head, relation=relation, tail=tail,
                                      source_memory_id=source_memory_id))
    return triples


def extract_entities(text: str) -> dict[str, list[str]]:
    """Return {label: [spans]} for the fixed ENTITY_LABELS (for node discovery)."""
    if not text or not text.strip():
        return {}
    m = _load()
    out = m.extract_entities(text, ENTITY_LABELS)
    return out.get("entities", {}) if isinstance(out, dict) else {}


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else (
        "The 088 migration deploys email-intelligence for ARC-774 but cannot "
        "apply to cloud yet due to a dba credential gap."
    )
    print("entities:", extract_entities(t))
    for tr in extract(t):
        print(f"  ({tr.head}) -[{tr.relation}]-> ({tr.tail})")
