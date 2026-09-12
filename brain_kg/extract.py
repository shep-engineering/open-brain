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


# The KG runs on the BRAIN GPU (RTX 3080 Ti), co-located with the ollama embedder,
# leaving the RTX 5090 free for Shep's other work/gaming (fact 895). We pick by
# NAME so it's correct even if the CUDA index order ever changes.
_BRAIN_GPU_NAME = os.getenv("OPEN_BRAIN_KG_GPU_NAME", "3080")


def _resolve_device() -> str:
    """Return the torch device string for GLiNER2. Targets the BRAIN GPU (3080 Ti)
    by name, NOT the 5090. OPEN_BRAIN_KG_DEVICE overrides ('cuda:1'|'cpu'|...).
    Falls back to cpu with a loud warning if no CUDA (never silent-CPU again)."""
    import sys as _sys
    forced = os.getenv("OPEN_BRAIN_KG_DEVICE")
    if forced:
        return forced
    try:
        import torch
        if torch.cuda.is_available():
            # find the index whose name contains the brain-GPU marker (e.g. "3080")
            for i in range(torch.cuda.device_count()):
                if _BRAIN_GPU_NAME.lower() in torch.cuda.get_device_name(i).lower():
                    return f"cuda:{i}"
            # brain GPU not found — use the LOWEST-memory GPU (avoid the 5090) so we
            # don't grab the big card by default.
            idxs = sorted(range(torch.cuda.device_count()),
                          key=lambda i: torch.cuda.get_device_properties(i).total_memory)
            if idxs:
                print(f"WARNING: brain GPU '{_BRAIN_GPU_NAME}' not found by name; "
                      f"using smallest GPU cuda:{idxs[0]} to spare the big card.",
                      file=_sys.stderr)
                return f"cuda:{idxs[0]}"
    except Exception:
        pass
    print("WARNING: CUDA torch not available — GLiNER2 falling back to CPU "
          "(this pins the CPU + thermal-throttles). Install CUDA torch for the GPU.",
          file=_sys.stderr)
    return "cpu"


# Minimum free VRAM (MiB) on the target GPU before we load GLiNER2. The 3080 Ti
# is shared with the embedder and often has ~1GB free; loading into too little
# headroom OOMs or thrashes the embedder. Preflight-check and refuse loudly.
_MIN_FREE_VRAM_MIB = int(os.getenv("OPEN_BRAIN_KG_MIN_FREE_VRAM_MIB", "1800"))


def _check_vram(device: str) -> None:
    """Refuse to load onto a GPU with too little free VRAM (co-tenant safety)."""
    if not device.startswith("cuda"):
        return
    import sys as _sys
    try:
        import torch
        idx = int(device.split(":")[1]) if ":" in device else 0
        free_b, _total_b = torch.cuda.mem_get_info(idx)
        free_mib = free_b // (1024 * 1024)
        if free_mib < _MIN_FREE_VRAM_MIB:
            raise RuntimeError(
                f"Only {free_mib} MiB free on {device} "
                f"({torch.cuda.get_device_name(idx)}); need >= {_MIN_FREE_VRAM_MIB}. "
                f"The embedder is using it — run extraction when it has headroom, "
                f"or set OPEN_BRAIN_KG_MIN_FREE_VRAM_MIB lower to override (risks OOM)."
            )
        print(f"[brain_kg.extract] VRAM preflight OK: {free_mib} MiB free on {device}",
              file=_sys.stderr)
    except RuntimeError:
        raise
    except Exception as exc:  # pragma: no cover - can't check; proceed with a note
        print(f"[brain_kg.extract] VRAM preflight skipped ({exc})", file=_sys.stderr)


def _load():
    """Load GLiNER2 once (process-wide) onto the GPU when available. Logs the
    device at load time so device placement is NEVER ambiguous (handoff root-cause).
    Refuses to load if the target GPU lacks free VRAM (co-tenant safety)."""
    global _model
    if _model is None:
        import sys as _sys
        from gliner2 import GLiNER2  # lazy: non-extract paths don't need torch
        device = _resolve_device()
        _check_vram(device)
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
