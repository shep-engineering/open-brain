"""Tests for the split embed/metadata ollama base URLs (dual-GPU routing).

OLLAMA_EMBED_BASE_URL lets embeddings hit a dedicated ollama instance pinned to
a second GPU (the RTX 3080 Ti on :11435), while metadata/generation stays on
OLLAMA_BASE_URL (:11434, RTX 5090). When unset it must fall back to
OLLAMA_BASE_URL so single-instance setups are unaffected.

Pure config resolution. `brain_v2.config` calls load_dotenv() at import, so we
neutralize it here to test purely from the process environment (otherwise the
developer's real .env leaks in and makes the fallback case non-deterministic).

Run with: pytest tests/test_embed_base_url.py -v
"""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    # Stop brain_v2.config's `from dotenv import load_dotenv; load_dotenv(.env)`
    # from re-injecting the real .env on reload, so env control is deterministic.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)


def _reload_v2_config():
    import brain_v2.config as c
    return importlib.reload(c)


def test_embed_base_url_defaults_to_base(monkeypatch):
    monkeypatch.delenv("OLLAMA_EMBED_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host-a:11434")
    c = _reload_v2_config()
    assert c.OLLAMA_EMBED_BASE_URL == "http://host-a:11434"


def test_embed_base_url_override_is_independent(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host-a:11434")
    monkeypatch.setenv("OLLAMA_EMBED_BASE_URL", "http://host-b:11435")
    c = _reload_v2_config()
    assert c.OLLAMA_EMBED_BASE_URL == "http://host-b:11435"
    # metadata URL must NOT be affected by the embed override
    assert c.OLLAMA_BASE_URL == "http://host-a:11434"


def test_embedding_module_uses_embed_url(monkeypatch):
    # brain_v2.embedding must build its /api/embeddings request from the embed URL.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host-a:11434")
    monkeypatch.setenv("OLLAMA_EMBED_BASE_URL", "http://host-b:11435")
    _reload_v2_config()
    import brain_v2.embedding as e
    importlib.reload(e)
    assert e.OLLAMA_EMBED_BASE_URL == "http://host-b:11435"
