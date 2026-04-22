"""
Open Brain v2 — Observability Layer
====================================
Structured JSONL logging, per-tool metrics, error tracking, and alert hooks.
Ported from v1's observability.py with v2-specific paths.

Usage in server.py:
    from brain_v2.observability import obs

    obs.startup(version="0.22.0")

    # Inside _instrument decorator:
    obs.record_call(tool, duration_ms, success=True)
    obs.record_slow_call(tool, duration_ms, threshold_ms=10000)

Public API:
    obs.record_call(tool, duration_ms, success, error=None, meta=None)
    obs.record_error(context, exc)
    obs.record_slow_call(tool, duration_ms, threshold_ms=10000)
    obs.get_metrics()       -> dict for dashboard / metrics_v2 tool
    obs.get_recent_events() -> list of last N log entries
    obs.get_recent_errors() -> list of recent ERROR entries
    obs.startup()           -> call once at server startup
    obs.shutdown()          -> call on clean exit
"""
from __future__ import annotations

import collections
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent.parent.resolve()  # repo root
LOG_DIR   = BASE_DIR / "logs"
LOG_FILE  = LOG_DIR / "brain_v2.jsonl"
MAX_BYTES = 5 * 1024 * 1024   # 5 MB per log file
MAX_FILES = 5
RING_SIZE = 500                # in-memory ring buffer for dashboard

# ── Internal state ────────────────────────────────────────────────────────────

_lock   = threading.Lock()
_ring   = collections.deque(maxlen=RING_SIZE)
_counts: dict[str, int]    = collections.defaultdict(int)
_errors: dict[str, int]    = collections.defaultdict(int)
_times:  dict[str, list]   = collections.defaultdict(list)  # last 50 durations per tool
_startup_time: float       = 0.0

# ── Rotating file handler ─────────────────────────────────────────────────────

def _rotate_if_needed():
    """Rotate log file if it exceeds MAX_BYTES."""
    if not LOG_FILE.exists():
        return
    if LOG_FILE.stat().st_size < MAX_BYTES:
        return
    for i in range(MAX_FILES - 1, 0, -1):
        src = LOG_DIR / f"brain_v2.{i}.jsonl"
        dst = LOG_DIR / f"brain_v2.{i+1}.jsonl"
        if src.exists():
            src.rename(dst)
    LOG_FILE.rename(LOG_DIR / "brain_v2.1.jsonl")


def _write(entry: dict):
    """Write a JSON log entry to file and ring buffer."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed()
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    with _lock:
        _ring.append(entry)


# ── Core logger ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _log(level: str, event: str, **kwargs):
    entry = {"ts": _now_iso(), "level": level, "event": event, **kwargs}
    _write(entry)
    print(f"[brain_v2] {level} {event}" +
          (f" {kwargs}" if kwargs else ""), file=sys.stderr)


# ── Public API ────────────────────────────────────────────────────────────────

class Observability:
    """Central observability object. Import as `obs` from this module."""

    def startup(self, version: str = "unknown"):
        global _startup_time
        _startup_time = time.time()
        _log("INFO", "server.startup", version=version,
             pid=os.getpid(), python=sys.version.split()[0])

    def shutdown(self, reason: str = "normal"):
        uptime = time.time() - _startup_time if _startup_time else 0
        _log("INFO", "server.shutdown", reason=reason, uptime_s=round(uptime, 1))

    def record_call(self, tool: str, duration_ms: float,
                    success: bool, error: Optional[str] = None,
                    meta: Optional[dict] = None):
        with _lock:
            _counts[tool] += 1
            if not success:
                _errors[tool] += 1
            buf = _times[tool]
            buf.append(duration_ms)
            if len(buf) > 50:
                buf.pop(0)

        entry: dict[str, Any] = {
            "ts": _now_iso(),
            "level": "ERROR" if not success else "INFO",
            "event": "tool.call",
            "tool": tool,
            "duration_ms": round(duration_ms, 2),
            "success": success,
        }
        if error:
            entry["error"] = error
        if meta:
            entry.update(meta)
        _write(entry)

        if not success:
            self._alert(tool, error or "unknown error")

    def record_error(self, context: str, exc: Exception):
        tb = traceback.format_exc()
        _log("ERROR", "unhandled.error", context=context,
             exc_type=type(exc).__name__, exc=str(exc), traceback=tb)
        self._alert(context, f"{type(exc).__name__}: {exc}")

    def record_db_error(self, operation: str, exc: Exception):
        _log("ERROR", "db.error", operation=operation,
             exc_type=type(exc).__name__, exc=str(exc))

    def record_embedding_error(self, exc: Exception):
        _log("ERROR", "embedding.error",
             exc_type=type(exc).__name__, exc=str(exc))

    def record_slow_call(self, tool: str, duration_ms: float, threshold_ms: float = 10000):
        if duration_ms > threshold_ms:
            _log("WARN", "tool.slow", tool=tool,
                 duration_ms=round(duration_ms, 2), threshold_ms=threshold_ms)

    def info(self, event: str, **kwargs):
        _log("INFO", event, **kwargs)

    def warn(self, event: str, **kwargs):
        _log("WARN", event, **kwargs)

    def error(self, event: str, **kwargs):
        _log("ERROR", event, **kwargs)

    def get_metrics(self) -> dict:
        """Return a metrics snapshot for the dashboard / metrics_v2 tool."""
        with _lock:
            counts  = dict(_counts)
            errors  = dict(_errors)
            avg_ms  = {
                tool: round(sum(v) / len(v), 1)
                for tool, v in _times.items() if v
            }
            p99_ms  = {
                tool: round(sorted(v)[int(len(v) * 0.99)], 1)
                for tool, v in _times.items() if len(v) >= 2
            }

        total_calls  = sum(counts.values())
        total_errors = sum(errors.values())
        error_rate   = round(total_errors / total_calls * 100, 1) if total_calls else 0.0
        uptime       = time.time() - _startup_time if _startup_time else 0

        return {
            "uptime_s":     round(uptime),
            "total_calls":  total_calls,
            "total_errors": total_errors,
            "error_rate":   error_rate,
            "by_tool":      counts,
            "errors_by_tool": errors,
            "avg_ms":       avg_ms,
            "p99_ms":       p99_ms,
        }

    def get_recent_events(self, n: int = 50, level: Optional[str] = None) -> list:
        with _lock:
            events = list(_ring)
        if level:
            events = [e for e in events if e.get("level") == level]
        return events[-n:]

    def get_recent_errors(self, n: int = 20) -> list:
        return self.get_recent_events(n=n, level="ERROR")

    def tail_log(self, n: int = 30) -> list[str]:
        """Return last N lines from the JSONL log file."""
        if not LOG_FILE.exists():
            return []
        try:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            return [line for line in lines if line.strip()][-n:]
        except Exception:
            return []

    def _alert(self, context: str, message: str):
        """Fire alert hooks: desktop notification + JSONL log."""
        _log("ALERT", "alert.fired", context=context, message=message[:200])
        _toast(f"Open Brain v2 Error: {context}", message[:120])


# ── Desktop notification (Windows) ───────────────────────────────────────────

def _toast(title: str, message: str):
    """Show a Windows desktop notification. Silent fail if unavailable."""
    try:
        from plyer import notification
        notification.notify(
            title=title, message=message,
            app_name="Open Brain v2", timeout=8,
        )
        return
    except Exception:
        pass
    try:
        import subprocess
        ps = (
            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, '
            f'ContentType = WindowsRuntime] | Out-Null; '
            f'$t = [Windows.UI.Notifications.ToastNotificationManager]::'
            f'GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); '
            f'$t.GetElementsByTagName("text")[0].AppendChild($t.CreateTextNode("{title}")) | Out-Null; '
            f'$t.GetElementsByTagName("text")[1].AppendChild($t.CreateTextNode("{message}")) | Out-Null; '
            f'$n = [Windows.UI.Notifications.ToastNotification]::new($t); '
            f'[Windows.UI.Notifications.ToastNotificationManager]::'
            f'CreateToastNotifier("OpenBrainV2").Show($n)'
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ── Singleton ─────────────────────────────────────────────────────────────────

obs = Observability()
