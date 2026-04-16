"""Embedding via Ollama nomic-embed-text (same host as v1 uses).

Kept intentionally minimal. v2 does NOT call the metadata LLM —
no smart-merge, no auto-classification. Type + headline are required
at write time from the caller, which eliminates one of the two
/api/generate triggers per write documented in infra-cost-addendum §2.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL


class EmbeddingError(RuntimeError):
    pass


def embed(text: str) -> list[float]:
    if not text or not text.strip():
        raise EmbeddingError("cannot embed empty text")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        data=json.dumps({"model": OLLAMA_EMBED_MODEL, "prompt": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise EmbeddingError(
            f"Ollama embeddings HTTP {e.code}: {body}. "
            f"Pull the model: ollama pull {OLLAMA_EMBED_MODEL}"
        ) from e
    except urllib.error.URLError as e:
        raise EmbeddingError(
            f"Ollama unreachable at {OLLAMA_BASE_URL}: {e.reason}"
        ) from e
    vec = payload.get("embedding")
    if not vec:
        raise EmbeddingError(f"Ollama returned no embedding: {payload}")
    return vec


def embed_to_pgvector(text: str) -> str:
    """Return a string literal formatted for pgvector '[x,y,z]' insertion."""
    vec = embed(text)
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
