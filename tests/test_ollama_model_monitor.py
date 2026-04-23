"""Tests for scripts/ollama_model_monitor.py.

Two tiers:
  - Unit: compute_transitions() is a pure function. Drive it directly
    with prior-state + current-ps input dicts and assert events.
  - Integration: hit the REAL running Ollama at localhost:11434 via
    _fetch_loaded_models(). Skipped if Ollama isn't reachable. End-to-end
    tests spawn the monitor as a subprocess, drive a real load via the
    /api/generate endpoint, and assert a LOAD event appears in the JSONL.

Run all: pytest tests/test_ollama_model_monitor.py -v
Run unit only: pytest tests/test_ollama_model_monitor.py -v -m "not slow"
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(__file__)
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import ollama_model_monitor as mon  # noqa: E402


# ==========================================================
# 1. Unit — compute_transitions state machine
# ==========================================================


def test_initial_load_emits_load_no_gap():
    """Fresh monitor, no prior state: first appearance of a model emits
    LOAD with gap_since_unload_s=None."""
    events, new_state = mon.compute_transitions(
        prev_state={},
        current={"qwen2.5:32b": {"digest": "abc", "size_vram": 33 * 2**30,
                                  "expires_at": "2026-04-23T08:00:00Z"}},
        now_iso="2026-04-23T07:55:00+00:00",
        thrash_window_seconds=300,
    )
    kinds = [e["event"] for e in events]
    assert kinds == ["LOAD"]
    assert events[0]["model"] == "qwen2.5:32b"
    assert events[0]["gap_since_unload_s"] is None
    assert new_state["qwen2.5:32b"]["loaded_at"] == "2026-04-23T07:55:00+00:00"


def test_still_loaded_emits_nothing_but_refreshes_expires_at():
    """Next poll with the same model loaded: no event, expires_at bumps."""
    prev = {"qwen2.5:32b": {
        "loaded_at":      "2026-04-23T07:55:00+00:00",
        "last_unload_at": None,
        "digest":         "abc",
        "size_vram":      33 * 2**30,
        "expires_at":     "2026-04-23T08:00:00Z",
    }}
    events, new_state = mon.compute_transitions(
        prev_state=prev,
        current={"qwen2.5:32b": {"digest": "abc", "size_vram": 33 * 2**30,
                                  "expires_at": "2026-04-23T08:05:00Z"}},
        now_iso="2026-04-23T07:56:00+00:00",
        thrash_window_seconds=300,
    )
    assert events == []
    assert new_state["qwen2.5:32b"]["expires_at"] == "2026-04-23T08:05:00Z"


def test_unload_emits_unload_with_lived_seconds():
    """Model was loaded for 120s then disappears from current — UNLOAD
    with lived_s=120."""
    prev = {"qwen2.5:32b": {
        "loaded_at":      "2026-04-23T07:55:00+00:00",
        "last_unload_at": None,
        "digest":         "abc",
        "size_vram":      33 * 2**30,
        "expires_at":     "2026-04-23T08:00:00Z",
    }}
    events, new_state = mon.compute_transitions(
        prev_state=prev,
        current={},
        now_iso="2026-04-23T07:57:00+00:00",
        thrash_window_seconds=300,
    )
    kinds = [e["event"] for e in events]
    assert kinds == ["UNLOAD"]
    assert events[0]["lived_s"] == 120
    # State retains last_unload_at for future gap calculation.
    assert new_state["qwen2.5:32b"]["loaded_at"] is None
    assert new_state["qwen2.5:32b"]["last_unload_at"] == "2026-04-23T07:57:00+00:00"


def test_reload_within_thrash_window_emits_thrash():
    """Model unloaded 60s ago, now loads again — both LOAD (gap=60) and
    THRASH events fire."""
    prev = {"qwen2.5:32b": {
        "loaded_at":      None,
        "last_unload_at": "2026-04-23T07:56:00+00:00",
        "digest":         "abc",
        "size_vram":      None,
        "expires_at":     None,
    }}
    events, new_state = mon.compute_transitions(
        prev_state=prev,
        current={"qwen2.5:32b": {"digest": "abc", "size_vram": 33 * 2**30,
                                  "expires_at": "2026-04-23T08:02:00Z"}},
        now_iso="2026-04-23T07:57:00+00:00",
        thrash_window_seconds=300,
    )
    kinds = [e["event"] for e in events]
    assert kinds == ["LOAD", "THRASH"]
    assert events[0]["gap_since_unload_s"] == 60
    assert events[1]["gap_s"] == 60
    assert events[1]["threshold"] == 300
    assert new_state["qwen2.5:32b"]["loaded_at"] == "2026-04-23T07:57:00+00:00"


def test_reload_outside_thrash_window_no_thrash():
    """Gap longer than thrash_window → LOAD without THRASH."""
    prev = {"qwen2.5:32b": {
        "loaded_at":      None,
        "last_unload_at": "2026-04-23T07:00:00+00:00",
        "digest":         "abc",
        "size_vram":      None,
        "expires_at":     None,
    }}
    events, _new = mon.compute_transitions(
        prev_state=prev,
        current={"qwen2.5:32b": {"digest": "abc", "size_vram": 33 * 2**30,
                                  "expires_at": "2026-04-23T08:10:00Z"}},
        now_iso="2026-04-23T08:00:00+00:00",
        thrash_window_seconds=300,
    )
    kinds = [e["event"] for e in events]
    assert kinds == ["LOAD"]
    assert events[0]["gap_since_unload_s"] == 3600


def test_multiple_models_simultaneous_transitions():
    """Model A unloads, model B loads in same tick — two events."""
    prev = {
        "modelA": {"loaded_at": "2026-04-23T07:50:00+00:00",
                    "last_unload_at": None, "digest": "A",
                    "size_vram": 1, "expires_at": None},
    }
    events, new_state = mon.compute_transitions(
        prev_state=prev,
        current={"modelB": {"digest": "B", "size_vram": 2,
                             "expires_at": "2026-04-23T08:10:00Z"}},
        now_iso="2026-04-23T07:55:00+00:00",
        thrash_window_seconds=300,
    )
    kinds = sorted(e["event"] for e in events)
    assert kinds == ["LOAD", "UNLOAD"]
    by_model = {(e["event"], e["model"]) for e in events}
    assert ("UNLOAD", "modelA") in by_model
    assert ("LOAD",   "modelB") in by_model
    assert new_state["modelA"]["loaded_at"] is None
    assert new_state["modelB"]["loaded_at"] == "2026-04-23T07:55:00+00:00"


def test_bad_iso_timestamp_does_not_crash_gap_calc():
    """Malformed prior last_unload_at still returns a LOAD event with
    gap=None rather than raising."""
    prev = {"qwen2.5:32b": {
        "loaded_at":      None,
        "last_unload_at": "not-a-timestamp",
        "digest":         "abc",
        "size_vram":      None,
        "expires_at":     None,
    }}
    events, _new = mon.compute_transitions(
        prev_state=prev,
        current={"qwen2.5:32b": {"digest": "abc", "size_vram": 1,
                                  "expires_at": None}},
        now_iso="2026-04-23T08:00:00+00:00",
        thrash_window_seconds=300,
    )
    assert [e["event"] for e in events] == ["LOAD"]
    assert events[0]["gap_since_unload_s"] is None


# ==========================================================
# 2. Unit — _fetch_loaded_models error handling
# ==========================================================


def test_fetch_returns_empty_on_unreachable_host(monkeypatch):
    """Point at a guaranteed-unroutable URL; function must return {}
    without raising."""
    monkeypatch.setattr(mon, "OLLAMA_URL", "http://127.0.0.1:1")  # port 1 is reserved
    assert mon._fetch_loaded_models() == {}


def test_fetch_returns_empty_on_bad_json(monkeypatch):
    """Server returns non-JSON — caught and treated as no models."""
    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b"not json at all"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp())
    assert mon._fetch_loaded_models() == {}


# ==========================================================
# 3. Integration — hit the real running Ollama
# ==========================================================


def _ollama_reachable() -> bool:
    try:
        req = urllib.request.Request(f"{mon.OLLAMA_URL}/api/version")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


pytestmark_real = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama not reachable at localhost:11434 — skipping integration tests",
)


@pytestmark_real
def test_real_fetch_returns_list_shape():
    """Call the real Ollama /api/ps endpoint and assert the dict shape
    our consumer expects."""
    out = mon._fetch_loaded_models()
    # Shape: {name: {expires_at, size_vram, digest}}. Empty if nothing loaded.
    assert isinstance(out, dict)
    for name, info in out.items():
        assert isinstance(name, str) and name
        assert isinstance(info, dict)
        assert "expires_at" in info
        assert "size_vram"  in info
        assert "digest"     in info


@pytestmark_real
@pytest.mark.slow
def test_monitor_subprocess_detects_real_load(tmp_path):
    """End-to-end: run the monitor as a subprocess against real Ollama,
    trigger a model load via /api/generate with a small model, and
    confirm a LOAD event lands in the JSONL.

    We use a tiny model to keep the test fast — default fallback is
    whatever Ollama already has loaded or can load quickly. If no
    lightweight model is available, we skip.
    """
    # Find a lightweight model to load. We prefer nomic-embed-text (embedding,
    # ~300 MB) if available; otherwise skip — we won't thrash a 32B just for a test.
    try:
        req = urllib.request.Request(f"{mon.OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        pytest.skip(f"couldn't list ollama models: {exc}")

    available = [m.get("name") or m.get("model") for m in tags.get("models") or []]
    light = None
    for candidate in ("nomic-embed-text:latest", "nomic-embed-text",
                        "all-minilm:latest", "all-minilm"):
        if candidate in available:
            light = candidate
            break
    if not light:
        pytest.skip("no lightweight embedding model available for load test")

    # First ensure the model is NOT loaded (send an unload via keep_alive=0).
    try:
        payload = json.dumps({"model": light, "prompt": "",
                               "stream": False, "keep_alive": 0}).encode()
        req = urllib.request.Request(
            f"{mon.OLLAMA_URL}/api/generate", data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except urllib.error.HTTPError:
        # Embedding-only models reject /api/generate; use /api/embeddings unload style
        pass
    except Exception:
        pass

    # Wait a beat for unload to settle.
    time.sleep(2)
    baseline = mon._fetch_loaded_models()
    if light in baseline:
        pytest.skip(f"{light} stayed loaded despite keep_alive=0 — can't reliably test load event")

    log_path = tmp_path / "events.jsonl"
    env = dict(os.environ)
    env["OLLAMA_POLL_SECONDS"]  = "2"
    env["OLLAMA_THRASH_WINDOW"] = "600"
    env["OLLAMA_MONITOR_LOG"]   = str(log_path)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO, "scripts", "ollama_model_monitor.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        # Give monitor time to emit baseline.
        time.sleep(4)

        # Trigger a real load. Embeddings API loads the model.
        embed_payload = json.dumps({"model": light, "prompt": "hello"}).encode()
        req = urllib.request.Request(
            f"{mon.OLLAMA_URL}/api/embeddings", data=embed_payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=30).read()

        # Poll the JSONL for a LOAD event. Give the monitor up to 20s.
        deadline = time.time() + 20
        load_event = None
        while time.time() < deadline:
            if log_path.exists():
                with open(log_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        if ev.get("event") == "LOAD" and ev.get("model") == light:
                            load_event = ev
                            break
            if load_event:
                break
            time.sleep(1)

        assert load_event is not None, (
            f"monitor did not emit LOAD for {light} within 20s of embeddings call. "
            f"JSONL content:\n{log_path.read_text() if log_path.exists() else '<empty>'}"
        )
        assert load_event["model"] == light
        # We reset unload above, so gap_since_unload_s should be None (fresh start).
        # But if the monitor's own baseline captured the model briefly due to timing,
        # we don't enforce that strictly — just assert the event fired.
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
