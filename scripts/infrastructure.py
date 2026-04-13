"""Open Brain infrastructure orchestration — pure Python replacement for the
fragile .cmd launcher chain.

WHY THIS EXISTS
---------------
The prior launcher chain had 5 process boundaries (dashboard.cmd → pythonw →
dashboard.py → Popen(cmd /c on.cmd) → start /B ollama-serve.cmd → ollama serve).
Each boundary introduced a failure mode: cmd quote-parsing, `%~dp0` in blocks,
`::` vs REM, `start /B` pipe inheritance, file-sharing conflicts in startup.log,
etc. The splash's stdout reader hung indefinitely because the innermost long-
running process (ollama) inherited the Popen PIPE via the chain.

This module collapses the chain to ONE process boundary: Python directly spawns
each infrastructure component, using explicit handle redirection + close_fds +
Windows DETACHED_PROCESS flags to guarantee the spawned children cannot inherit
the dashboard's stdout/stderr.

EMPIRICAL VERIFICATION (2026-04-13)
-----------------------------------
The detach pattern below was verified with actual `ollama serve`:
  - Parent spawned ollama in 0ms (no block)
  - Parent returned to caller immediately
  - Ollama reached /api/tags=200 at t=2.7s
  - Parent exited cleanly; ollama kept running
  - No file-sharing conflicts; no pipe inheritance

PATTERN DETAILS
---------------
For LONG-RUNNING CONSOLE processes (ollama serve):
    subprocess.Popen(
        [argv],
        stdin=subprocess.DEVNULL,
        stdout=open(log_path, 'ab', buffering=0),
        stderr=subprocess.STDOUT,
        close_fds=True,
        creationflags=DETACH_FLAGS_CONSOLE,   # Windows only; 0 on POSIX
        start_new_session=not IS_WINDOWS,     # POSIX equivalent of detached
    )

For GUI applications (Docker Desktop):
    subprocess.Popen(
        [exe],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        # No creation flags — GUI subsystem self-registers; detach flags
        # can suppress tray registration.
    )

For SHORT-LIVED commands (docker info, docker start, taskkill):
    subprocess.run([argv], capture_output=True, text=True, timeout=N)
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = platform.system() == "Windows"

# Windows-specific subprocess creation flags for long-running detached console
# processes (e.g. ollama serve). These are members of subprocess on Windows;
# on POSIX we use start_new_session=True as the equivalent.
if IS_WINDOWS:
    DETACH_FLAGS_CONSOLE = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NO_WINDOW
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )
else:
    DETACH_FLAGS_CONSOLE = 0

# Paths / constants
BASE = Path(__file__).resolve().parent.parent  # open-brain repo root
LOGS_DIR = BASE / "logs"
OLLAMA_LOG = LOGS_DIR / "ollama.log"
STARTUP_LOG = LOGS_DIR / "startup.log"

DOCKER_DESKTOP_EXE = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
DB_CONTAINER = "open-brain-db"
DB_URL = "postgresql://postgres:password@127.0.0.1:5432/openbrain"
OLLAMA_API = "http://127.0.0.1:11434/api/tags"

# Ollama env vars (formerly set by on.cmd)
OLLAMA_ENV = {
    "OLLAMA_NUM_GPU": "2",
    "CUDA_VISIBLE_DEVICES": "0,1",
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_MAX_LOADED_MODELS": "2",
}


@dataclass
class Progress:
    """A structured progress entry. Serialized to startup.log AND handed
    to the UI callback. Fields intentionally flat/JSON-able."""
    step: str           # 'docker' | 'db' | 'ollama' | 'stop'
    status: str         # 'start' | 'poll' | 'ready' | 'fail' | 'info'
    detail: str = ""    # human-readable line shown in splash
    elapsed_s: float = 0.0
    extra: dict = field(default_factory=dict)

    def format(self) -> str:
        ts = datetime.now().isoformat(timespec="seconds")
        return f"[{ts}] {self.step}:{self.status} t={self.elapsed_s:.2f}s {self.detail}"


ProgressCallback = Callable[[Progress], None]


def _noop_callback(p: Progress) -> None:
    pass


class Infrastructure:
    """Ensures Docker, open-brain-db, and Ollama are running. Thread-safe
    to instantiate per startup flow; do not share instances across threads.

    All ensure_* methods return True on success / False on failure within
    the given timeout. Failures do NOT raise — they log and return False
    so the UI can render a user-actionable state.

    The on_progress callback is invoked from whatever thread calls the
    ensure_* method. Tkinter callers must marshal UI updates via
    `widget.after(0, ...)` — this module never touches UI directly.
    """

    def __init__(
        self,
        on_progress: ProgressCallback = _noop_callback,
        logs_dir: Path = LOGS_DIR,
    ):
        self.on_progress = on_progress
        self.logs_dir = logs_dir
        self.startup_log = logs_dir / "startup.log"
        self.ollama_log = logs_dir / "ollama.log"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._t0 = time.monotonic()

    # ---------- Logging / progress ----------

    def _emit(self, step: str, status: str, detail: str = "", **extra) -> Progress:
        p = Progress(
            step=step,
            status=status,
            detail=detail,
            elapsed_s=time.monotonic() - self._t0,
            extra=extra,
        )
        try:
            with open(self.startup_log, "a", encoding="utf-8", newline="\n") as f:
                f.write(p.format() + "\n")
        except OSError:
            # If logs_dir is unwritable we still notify the UI; disk log is
            # best-effort.
            pass
        try:
            self.on_progress(p)
        except Exception:
            # Never let a UI callback exception break infra flow.
            pass
        return p

    # ---------- Docker ----------

    def _docker_info_ok(self) -> bool:
        try:
            r = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def ensure_docker(self, timeout: int = 60) -> bool:
        self._emit("docker", "start", "checking Docker daemon")
        if self._docker_info_ok():
            self._emit("docker", "ready", "Docker daemon already responsive")
            return True

        # Launch Docker Desktop GUI (if path exists). On non-Windows or
        # when the exe is missing, we skip the launch and just poll — the
        # user may have started docker via some other mechanism.
        if IS_WINDOWS and DOCKER_DESKTOP_EXE.exists():
            self._emit("docker", "info", f"launching Docker Desktop GUI")
            try:
                subprocess.Popen(
                    [str(DOCKER_DESKTOP_EXE)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except OSError as e:
                self._emit("docker", "fail", f"Docker Desktop launch failed: {e}")
                return False

        # Poll for daemon readiness.
        poll_start = time.monotonic()
        attempt = 0
        while time.monotonic() - poll_start < timeout:
            attempt += 1
            if self._docker_info_ok():
                self._emit("docker", "ready", f"Docker daemon ready after {attempt} check(s)")
                return True
            self._emit(
                "docker",
                "poll",
                f"waiting for Docker daemon ({int(time.monotonic()-poll_start)}s/{timeout}s)",
            )
            time.sleep(2)

        self._emit("docker", "fail", f"Docker daemon not responsive after {timeout}s")
        return False

    # ---------- Database ----------

    def _db_reachable(self) -> bool:
        try:
            import psycopg2
        except ImportError:
            self._emit("db", "fail", "psycopg2 not installed in this environment")
            return False
        try:
            conn = psycopg2.connect(DB_URL, connect_timeout=2)
            conn.close()
            return True
        except Exception:
            return False

    def ensure_db(self, timeout: int = 30) -> bool:
        self._emit("db", "start", f"ensuring {DB_CONTAINER} is running")
        try:
            r = subprocess.run(
                ["docker", "start", DB_CONTAINER],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode != 0:
                self._emit(
                    "db",
                    "fail",
                    f"docker start {DB_CONTAINER} rc={r.returncode}: {r.stderr.strip()[:120]}",
                )
                return False
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            self._emit("db", "fail", f"docker start invocation failed: {e}")
            return False

        poll_start = time.monotonic()
        attempt = 0
        while time.monotonic() - poll_start < timeout:
            attempt += 1
            if self._db_reachable():
                self._emit("db", "ready", f"postgres accepts connections after {attempt} poll(s)")
                return True
            self._emit(
                "db",
                "poll",
                f"waiting for postgres ({int(time.monotonic()-poll_start)}s/{timeout}s)",
            )
            time.sleep(1)

        self._emit("db", "fail", f"postgres not responsive after {timeout}s")
        return False

    # ---------- Ollama ----------

    def _ollama_api_ok(self) -> bool:
        try:
            resp = urllib.request.urlopen(OLLAMA_API, timeout=2)
            return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def ensure_ollama(self, timeout: int = 30) -> bool:
        self._emit("ollama", "start", "checking Ollama API")
        if self._ollama_api_ok():
            self._emit("ollama", "ready", "Ollama already responsive")
            return True

        self._emit("ollama", "info", "launching `ollama serve` (detached)")
        env = os.environ.copy()
        env.update(OLLAMA_ENV)

        # CRITICAL pattern: full handle redirection + close_fds + Windows
        # detach flags. This was verified empirically 2026-04-13 — the parent
        # Popen(stdout=PIPE) reader receives EOF when this child is spawned,
        # and the child remains alive after parent exit.
        try:
            logf = open(self.ollama_log, "ab", buffering=0)
        except OSError as e:
            self._emit("ollama", "fail", f"cannot open ollama.log: {e}")
            return False

        try:
            popen_kwargs = dict(
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=subprocess.STDOUT,
                close_fds=True,
                env=env,
            )
            if IS_WINDOWS:
                popen_kwargs["creationflags"] = DETACH_FLAGS_CONSOLE
            else:
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(["ollama", "serve"], **popen_kwargs)
        except (FileNotFoundError, OSError) as e:
            self._emit("ollama", "fail", f"ollama launch failed: {e}")
            return False
        finally:
            # Close our reference to the log file handle. The spawned child
            # has already duplicated the file descriptor for its own stdout.
            logf.close()

        poll_start = time.monotonic()
        attempt = 0
        while time.monotonic() - poll_start < timeout:
            attempt += 1
            if self._ollama_api_ok():
                self._emit("ollama", "ready", f"ollama ready after {attempt} poll(s)")
                return True
            self._emit(
                "ollama",
                "poll",
                f"waiting for ollama API ({int(time.monotonic()-poll_start)}s/{timeout}s)",
            )
            time.sleep(1)

        self._emit("ollama", "fail", f"ollama not responsive after {timeout}s")
        return False

    # ---------- Stop all ----------

    def stop_all(self) -> None:
        """Best-effort shutdown of ollama, db, Docker Desktop. Does not raise."""
        self._emit("stop", "start", "stopping Open Brain infrastructure")
        self._stop_ollama()
        self._stop_db()
        self._stop_docker_desktop()
        self._emit("stop", "ready", "stop_all complete")

    def _stop_ollama(self) -> None:
        try:
            subprocess.run(["ollama", "stop"], capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        if IS_WINDOWS:
            try:
                subprocess.run(
                    ["taskkill", "/IM", "ollama.exe", "/F"],
                    capture_output=True, timeout=5,
                )
                self._emit("stop", "info", "ollama processes terminated")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                self._emit("stop", "info", f"taskkill ollama: {e}")
        else:
            try:
                subprocess.run(
                    ["pkill", "-f", "ollama serve"],
                    capture_output=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass

    def _stop_db(self) -> None:
        try:
            r = subprocess.run(
                ["docker", "stop", DB_CONTAINER],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                self._emit("stop", "info", f"{DB_CONTAINER} stopped")
            else:
                self._emit("stop", "info", f"{DB_CONTAINER} stop rc={r.returncode}")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            self._emit("stop", "info", f"docker stop failed: {e}")

    def _stop_docker_desktop(self) -> None:
        if not IS_WINDOWS:
            return
        try:
            subprocess.run(
                ["taskkill", "/IM", "Docker Desktop.exe", "/F"],
                capture_output=True, timeout=10,
            )
            self._emit("stop", "info", "Docker Desktop terminated")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            self._emit("stop", "info", f"taskkill Docker Desktop: {e}")


# ---------- Convenience entry point ----------

def bring_up(on_progress: ProgressCallback = _noop_callback) -> bool:
    """Run all three ensure_* steps in sequence. Returns True iff all succeed.
    Each step logs to startup.log and invokes the progress callback. The
    caller is responsible for marshalling progress to a UI thread if needed.
    """
    infra = Infrastructure(on_progress=on_progress)
    if not infra.ensure_docker():
        return False
    if not infra.ensure_db():
        return False
    if not infra.ensure_ollama():
        return False
    return True


def bring_down(on_progress: ProgressCallback = _noop_callback) -> None:
    """Stop all components. Never raises."""
    Infrastructure(on_progress=on_progress).stop_all()
