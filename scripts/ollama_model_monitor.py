"""Ollama model-load/unload monitor + thrash detector.

Polls the Ollama HTTP API (`/api/ps`) at a fixed interval and emits one
JSON line per state transition to `logs/ollama-model-events.jsonl`. Also
mirrors to stderr so a live `Monitor` or terminal tail surfaces events
in real time.

Event kinds:
  LOAD            — a model appeared in `ollama ps` that wasn't there before
  UNLOAD          — a model disappeared (expired or explicitly unloaded)
  THRASH          — a LOAD landed within THRASH_WINDOW seconds of a prior
                    UNLOAD for the same model (tight reload cycle)

Config via env:
  OLLAMA_URL          default http://localhost:11434
  OLLAMA_POLL_SECONDS default 5
  OLLAMA_THRASH_WINDOW default 300 (seconds)  — max UNLOAD→LOAD gap to call thrash
  OLLAMA_MONITOR_LOG  default <repo>/logs/ollama-model-events.jsonl

Safe to run as a long-lived daemon. Handles HTTP errors by continuing
silently. Intended for a scheduled task or the dashboard's startup
sequence, not the MCP-server process.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
POLL_SECONDS = int(os.getenv("OLLAMA_POLL_SECONDS", "5"))
THRASH_WINDOW = int(os.getenv("OLLAMA_THRASH_WINDOW", "300"))

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_LOG = os.path.join(_REPO_ROOT, "logs", "ollama-model-events.jsonl")
LOG_PATH = os.getenv("OLLAMA_MONITOR_LOG", _DEFAULT_LOG)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(event: dict) -> None:
    """Write one JSONL line. Mirror to stderr."""
    line = json.dumps(event)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        print(f"[monitor] log write failed: {exc}", file=sys.stderr)
    print(line, file=sys.stderr, flush=True)


def _fetch_loaded_models() -> dict:
    """Return {name: {expires_at, size_vram, digest}} for currently loaded models.
    Empty dict on any error (treated as 'no models loaded' — transient
    errors will self-correct on the next poll)."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/ps")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
            ConnectionError):
        return {}
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for m in data.get("models") or []:
        name = m.get("name") or m.get("model")
        if not name:
            continue
        out[name] = {
            "expires_at": m.get("expires_at"),
            "size_vram":  m.get("size_vram"),
            "digest":     (m.get("digest") or "")[:12],
        }
    return out


def compute_transitions(prev_state: dict, current: dict, now_iso: str,
                         thrash_window_seconds: int) -> tuple[list[dict], dict]:
    """Pure-function state-transition core. Drives the polling loop.

    Given the prior monitor state and the currently-loaded-model dict
    from `_fetch_loaded_models()`, compute the list of events that
    should be emitted and the new state to carry forward.

    Kept separate from the poll loop so the transition logic can be
    unit-tested without spawning subprocesses or mocking HTTP.

    Returns:
        (events, new_state)
        events: list of {"event": "LOAD"|"UNLOAD"|"THRASH", ...} dicts
        new_state: the state dict to use in the next tick
    """
    events: list[dict] = []
    new_state: dict = {k: dict(v) for k, v in prev_state.items()}

    # LOAD detection: model in `current` with no active loaded_at in state.
    for name, info in current.items():
        prior = new_state.get(name) or {}
        if prior.get("loaded_at"):
            # Still loaded — refresh tracked expires_at only.
            new_state[name] = {
                **prior,
                "expires_at": info.get("expires_at"),
                "size_vram":  info.get("size_vram"),
            }
            continue
        last_unload_at = prior.get("last_unload_at")
        gap_seconds: int | None = None
        if last_unload_at:
            try:
                prev_dt = datetime.fromisoformat(last_unload_at)
                now_dt  = datetime.fromisoformat(now_iso)
                gap_seconds = int((now_dt - prev_dt).total_seconds())
            except Exception:
                pass
        new_state[name] = {
            "loaded_at":      now_iso,
            "last_unload_at": last_unload_at,
            "digest":         info.get("digest"),
            "size_vram":      info.get("size_vram"),
            "expires_at":     info.get("expires_at"),
        }
        events.append({
            "event":              "LOAD",
            "timestamp":          now_iso,
            "model":              name,
            "digest":             info.get("digest"),
            "size_vram_gb":       (info.get("size_vram") or 0) / (1024 ** 3),
            "gap_since_unload_s": gap_seconds,
        })
        if gap_seconds is not None and gap_seconds < thrash_window_seconds:
            events.append({
                "event":     "THRASH",
                "timestamp": now_iso,
                "model":     name,
                "gap_s":     gap_seconds,
                "threshold": thrash_window_seconds,
                "note":      ("LOAD followed UNLOAD within "
                               f"{gap_seconds}s — reload thrash"),
            })

    # UNLOAD detection: model was loaded, now missing from current.
    for name in list(new_state.keys()):
        if name in current:
            continue
        if not new_state[name].get("loaded_at"):
            continue
        prev_loaded_at = new_state[name].get("loaded_at")
        new_state[name] = {
            "loaded_at":      None,
            "last_unload_at": now_iso,
            "digest":         new_state[name].get("digest"),
            "size_vram":      None,
            "expires_at":     None,
        }
        lived_s: int | None = None
        if prev_loaded_at:
            try:
                prev_dt = datetime.fromisoformat(prev_loaded_at)
                now_dt  = datetime.fromisoformat(now_iso)
                lived_s = int((now_dt - prev_dt).total_seconds())
            except Exception:
                pass
        events.append({
            "event":     "UNLOAD",
            "timestamp": now_iso,
            "model":     name,
            "lived_s":   lived_s,
        })

    return events, new_state


def main() -> int:
    _emit({
        "event": "MONITOR_START",
        "timestamp": _now_iso(),
        "ollama_url": OLLAMA_URL,
        "poll_seconds": POLL_SECONDS,
        "thrash_window_seconds": THRASH_WINDOW,
        "log_path": LOG_PATH,
    })

    # Prior state: {name: {loaded_at, last_unload_at, digest, ...}}
    state: dict[str, dict] = {}

    # Prime from current state without emitting LOAD for already-running
    # models — we only care about transitions observed during monitor run.
    current = _fetch_loaded_models()
    for name, info in current.items():
        state[name] = {
            "loaded_at":      _now_iso(),
            "last_unload_at": None,
            "digest":         info.get("digest"),
            "size_vram":      info.get("size_vram"),
            "expires_at":     info.get("expires_at"),
        }
    _emit({
        "event": "MONITOR_BASELINE",
        "timestamp": _now_iso(),
        "currently_loaded": list(state.keys()),
    })

    while True:
        try:
            time.sleep(POLL_SECONDS)
            current = _fetch_loaded_models()
            events, state = compute_transitions(
                state, current, _now_iso(), THRASH_WINDOW,
            )
            for ev in events:
                _emit(ev)
        except KeyboardInterrupt:
            _emit({"event": "MONITOR_STOP",
                   "timestamp": _now_iso(),
                   "reason": "KeyboardInterrupt"})
            return 0
        except Exception as exc:
            # Never die; log and continue.
            _emit({"event": "MONITOR_ERROR",
                   "timestamp": _now_iso(),
                   "error": str(exc)})


if __name__ == "__main__":
    sys.exit(main())
