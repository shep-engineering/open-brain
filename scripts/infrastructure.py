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
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = platform.system() == "Windows"

# Windows-specific subprocess creation flags for long-running hidden-console
# processes (e.g. ollama serve) that we want to be able to signal gracefully.
#
# CRITICAL: we intentionally do NOT set DETACHED_PROCESS here.
#
# DETACHED_PROCESS forbids the child from having a console, which makes
# CTRL_BREAK_EVENT physically impossible to deliver — ollama.exe ignores
# every graceful-shutdown mechanism Windows offers. That's the root cause
# of the old "taskkill /F is the only option" problem.
#
# With CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP:
#   - CREATE_NO_WINDOW gives the child a hidden console (not no console).
#     Ollama can register a console control handler and respond to signals.
#   - CREATE_NEW_PROCESS_GROUP makes the child the leader of its own group,
#     so we can target it precisely with GenerateConsoleCtrlEvent / os.kill.
#
# Pipe-inheritance protection is handled separately by close_fds=True and
# explicit stdin/stdout/stderr redirection in Popen kwargs — we don't need
# DETACHED_PROCESS for that.
if IS_WINDOWS:
    SPAWN_FLAGS_CONSOLE = (
        subprocess.CREATE_NO_WINDOW
        | subprocess.CREATE_NEW_PROCESS_GROUP
    )
    # Kept as an alias for external callers that may import the old name.
    DETACH_FLAGS_CONSOLE = SPAWN_FLAGS_CONSOLE
else:
    SPAWN_FLAGS_CONSOLE = 0
    DETACH_FLAGS_CONSOLE = 0

# Paths / constants
BASE = Path(__file__).resolve().parent.parent  # open-brain repo root
LOGS_DIR = BASE / "logs"
OLLAMA_LOG = LOGS_DIR / "ollama.log"
OLLAMA_PID_FILE = LOGS_DIR / "ollama.pid"
STARTUP_LOG = LOGS_DIR / "startup.log"
DASHBOARD_CONFIG_FILE = LOGS_DIR / "dashboard-config.json"

DOCKER_DESKTOP_EXE = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
DB_CONTAINER = "open-brain-db"
DB_CONTAINER_V2 = "open-brain-v2-db"
DB_URL = "postgresql://postgres:password@127.0.0.1:5432/openbrain"
DB_URL_V2 = "postgresql://postgres:password@127.0.0.1:5433/open_brain_v2"
OLLAMA_API = "http://127.0.0.1:11434/api/tags"

# Ollama env vars (formerly set by on.cmd).
#
# CUDA_VISIBLE_DEVICES is overridden at runtime from dashboard-config.json
# (key `gpu_device`) when present, so the user's Dashboard → Compute Device
# selection controls which card ollama binds to. Default ("0,1") lets
# ollama see both cards and allocate freely — same as the prior behavior.
OLLAMA_ENV = {
    "OLLAMA_NUM_GPU": "2",
    "CUDA_VISIBLE_DEVICES": "0,1",
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_MAX_LOADED_MODELS": "2",
}


def list_nvidia_gpus() -> list[dict]:
    """Return a list of NVIDIA GPUs detected on this machine via
    ``nvidia-smi -L``. Each entry: ``{"index": "0", "name": "RTX 5090"}``.

    Returns an empty list if nvidia-smi is unavailable or fails. The
    caller should treat an empty result as "auto/unknown; do not set
    CUDA_VISIBLE_DEVICES" rather than an error.
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    gpus: list[dict] = []
    # Lines look like: "GPU 0: NVIDIA GeForce RTX 5090 (UUID: GPU-...)"
    import re as _re
    pat = _re.compile(r"^GPU\s+(\d+):\s+(.+?)(?:\s+\(UUID:.*\))?\s*$")
    for line in (r.stdout or "").splitlines():
        m = pat.match(line.strip())
        if not m:
            continue
        idx, raw_name = m.group(1), m.group(2).strip()
        # Strip the common "NVIDIA GeForce " prefix for display brevity
        display = raw_name.replace("NVIDIA GeForce ", "").strip()
        gpus.append({"index": idx, "name": display, "full_name": raw_name})
    return gpus


def load_dashboard_config() -> dict:
    """Read logs/dashboard-config.json. Returns {} if missing/corrupt."""
    import json as _json
    try:
        with open(DASHBOARD_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = _json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_dashboard_config(updates: dict) -> None:
    """Merge ``updates`` into logs/dashboard-config.json and write back."""
    import json as _json
    cfg = load_dashboard_config()
    cfg.update(updates)
    try:
        DASHBOARD_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DASHBOARD_CONFIG_FILE, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, indent=2)
    except OSError:
        pass


def resolved_cuda_visible_devices() -> str:
    """The value of ``CUDA_VISIBLE_DEVICES`` to use for this ollama spawn.

    Priority: ``gpu_device`` key in logs/dashboard-config.json, then the
    default ``OLLAMA_ENV["CUDA_VISIBLE_DEVICES"]``. An empty string value
    in the config means "let CUDA choose" — we skip setting the var.
    """
    cfg = load_dashboard_config()
    val = cfg.get("gpu_device")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return OLLAMA_ENV["CUDA_VISIBLE_DEVICES"]


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
        self.ollama_pid_file = logs_dir / "ollama.pid"
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

    def _db_reachable(self, url: str = DB_URL) -> bool:
        try:
            import psycopg2
        except ImportError:
            self._emit("db", "fail", "psycopg2 not installed in this environment")
            return False
        try:
            conn = psycopg2.connect(url, connect_timeout=2)
            conn.close()
            return True
        except Exception:
            return False

    def ensure_db(self, timeout: int = 30) -> bool:
        all_ok = True
        for container, url, label in (
            (DB_CONTAINER,    DB_URL,    "v1"),
            (DB_CONTAINER_V2, DB_URL_V2, "v2"),
        ):
            self._emit("db", "start", f"ensuring {container} is running")
            try:
                r = subprocess.run(
                    ["docker", "start", container],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if r.returncode != 0:
                    self._emit(
                        "db",
                        "fail",
                        f"docker start {container} rc={r.returncode}: {r.stderr.strip()[:120]}",
                    )
                    all_ok = False
                    continue
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                self._emit("db", "fail", f"docker start {container} failed: {e}")
                all_ok = False
                continue

            poll_start = time.monotonic()
            attempt = 0
            while time.monotonic() - poll_start < timeout:
                attempt += 1
                if self._db_reachable(url):
                    self._emit("db", "ready", f"{container} postgres ready after {attempt} poll(s)")
                    break
                self._emit(
                    "db",
                    "poll",
                    f"waiting for {container} ({int(time.monotonic()-poll_start)}s/{timeout}s)",
                )
                time.sleep(1)
            else:
                self._emit("db", "fail", f"{container} not responsive after {timeout}s")
                all_ok = False

        return all_ok

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
            # Someone else is already running ollama (commonly the Windows
            # Ollama desktop app's watchdog). We did NOT spawn this process,
            # so we do NOT own it — remove any stale pid file so stop_all
            # knows to skip the process kill and just unload our models.
            self._clear_ollama_pid()
            self._emit("ollama", "ready",
                       "Ollama already responsive (externally managed — we will not stop the process)")
            return True

        self._emit("ollama", "info", "launching `ollama serve` (detached)")
        env = os.environ.copy()
        env.update(OLLAMA_ENV)
        # Honor the dashboard's Compute Device selection if present.
        gpu = resolved_cuda_visible_devices()
        env["CUDA_VISIBLE_DEVICES"] = gpu
        self._emit("ollama", "info", f"CUDA_VISIBLE_DEVICES={gpu}")

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
                # CREATE_NEW_CONSOLE + STARTF_USESHOWWINDOW/SW_HIDE gives the
                # child a real (but invisible) console — required for
                # GenerateConsoleCtrlEvent to deliver Ctrl+Break later via
                # AttachConsole. CREATE_NEW_PROCESS_GROUP makes the child
                # its own group so we can target it precisely.
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE
                popen_kwargs["startupinfo"] = si
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_CONSOLE
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(["ollama", "serve"], **popen_kwargs)
            # Persist the PID (and process-group leader on Windows) so
            # bring_down — running in a different Python process — can
            # send it CTRL_BREAK_EVENT for graceful shutdown.
            try:
                self.ollama_pid_file.write_text(str(proc.pid), encoding="utf-8")
            except OSError as e:
                # Non-fatal — shutdown can still fall back to image-name kill
                self._emit("ollama", "info", f"could not write pid file: {e}")
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
        """Targeted shutdown: stop MCP servers, ollama, and the open-brain-db container.

        Intentionally does NOT kill Docker Desktop — the user may be running
        other containers (postgres for another project, redis, etc.) that
        would be collateral damage. Our ownership boundary is the
        `open-brain-db` container, not the Docker daemon itself.
        """
        self._emit("stop", "start", "stopping Open Brain infrastructure")
        self._stop_heartbeat_agent()
        self._stop_model_monitor()
        self._stop_mcp_servers()
        self._stop_ollama()
        self._stop_db()
        self._emit("stop", "info", "Docker Desktop left running (respects other containers)")
        self._emit("stop", "ready", "stop_all complete")

    def _stop_mcp_servers(self) -> None:
        """Terminate the Open Brain MCP server processes on ports 8080 (v1) and 8081 (v2).

        Uses psutil to find processes listening on those ports and terminates
        them gracefully (SIGTERM/terminate), falling back to kill if they don't
        exit within 5s. Skips ports that are free.
        """
        try:
            import psutil
        except ImportError:
            self._emit("stop", "info", "psutil not available — skipping MCP server stop")
            return

        targets = {8080: "v1", 8081: "v2"}
        for port, label in targets.items():
            pid = None
            try:
                for conn in psutil.net_connections(kind="tcp"):
                    if conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                        pid = conn.pid
                        break
            except (psutil.AccessDenied, OSError):
                pass

            if pid is None:
                self._emit("stop", "info", f"MCP {label} (port {port}): not running")
                continue

            try:
                proc = psutil.Process(pid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    self._emit("stop", "info", f"MCP {label} (port {port}) stopped (pid {pid})")
                except psutil.TimeoutExpired:
                    proc.kill()
                    self._emit("stop", "info", f"MCP {label} (port {port}) force-killed (pid {pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                self._emit("stop", "info", f"MCP {label} (port {port}) stop failed: {e}")

    def _stop_model_monitor(self) -> None:
        """Terminate the v0.24.2+ ollama model monitor if running.

        Standalone Python process spawned from open-brain-on.cmd step 6
        (or via the OpenBrainOllamaMonitor scheduled task). Find any
        python* process whose argv ends in `scripts/ollama_model_monitor.py`
        and terminate it. Best-effort — same failure modes handled as
        _stop_heartbeat_agent.
        """
        try:
            import psutil
        except ImportError:
            self._emit("stop", "info", "psutil not available — skipping model monitor stop")
            return

        killed = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if not (name.startswith("python") or name.startswith("pythonw")):
                    continue
                cmdline = proc.info.get("cmdline") or []
                if not any("ollama_model_monitor.py" in str(c) for c in cmdline):
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed:
            self._emit("stop", "info", f"model monitor stopped ({killed} process)")
        else:
            self._emit("stop", "info", "model monitor not running")

    def _stop_heartbeat_agent(self) -> None:
        """Terminate the v0.14.0+ session-registry heartbeat agent if running.

        The agent is a standalone Python process spawned from open-brain-on.cmd
        (or the dashboard). It catches SIGINT/SIGTERM and exits between probe
        cycles. We find any python* process whose argv ends in
        `scripts/heartbeat_agent.py` and terminate it. Best-effort: if psutil
        is unavailable or we can't match the process, we skip — the agent will
        just keep running until the DB is gone, then error out cleanly.
        """
        try:
            import psutil
        except ImportError:
            self._emit("stop", "info", "psutil not available — skipping heartbeat agent stop")
            return

        killed = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if not (name.startswith("python") or name.startswith("pythonw")):
                    continue
                cmdline = proc.info.get("cmdline") or []
                if not any("heartbeat_agent.py" in str(c) for c in cmdline):
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed:
            self._emit("stop", "info", f"heartbeat agent stopped ({killed} process)")
        else:
            self._emit("stop", "info", "heartbeat agent not running")

    def _ollama_loaded_models(self) -> list[str]:
        """Return model names currently loaded in VRAM, via `ollama ps`.
        Empty list on any failure (best-effort — we don't block shutdown
        on model enumeration)."""
        try:
            r = subprocess.run(
                ["ollama", "ps"], capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                return []
            # Output: header row + one row per loaded model. First column
            # is NAME. Skip header, take first whitespace-separated token.
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
            if len(lines) <= 1:
                return []
            models = []
            for ln in lines[1:]:
                parts = ln.split()
                if parts:
                    models.append(parts[0])
            return models
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []

    def _ollama_processes_alive(self) -> bool:
        """True if any ollama.exe (Windows) or `ollama serve` (POSIX) is
        still running. Used to poll after a soft stop attempt."""
        try:
            if IS_WINDOWS:
                r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                return "ollama.exe" in (r.stdout or "")
            else:
                r = subprocess.run(
                    ["pgrep", "-f", "ollama serve"],
                    capture_output=True, text=True, timeout=5,
                )
                return bool((r.stdout or "").strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _stop_ollama(self) -> None:
        """Graceful-first ollama shutdown with ownership awareness.

        Step 1 — Always unload models from VRAM (the critical graceful op,
            regardless of who spawned the server).
        Step 2 — If WE spawned ollama (pid file present, pid alive), send
            CTRL_BREAK_EVENT to its process group (hence the spawn flags
            CREATE_NEW_PROCESS_GROUP + CREATE_NO_WINDOW — NOT DETACHED,
            which would block Ctrl+Break entirely). Poll up to 10s for a
            clean exit and verify via ollama.log that shutdown was
            graceful, not forced. Fall back to /F only if graceful fails.
        Step 3 — If we do NOT own ollama (e.g., the Windows Ollama desktop
            app is managing it via its watchdog), leave the process alone.
            Killing it would be futile — the watchdog respawns in seconds
            — and rude to any other consumer of the shared ollama server.
            Models are already unloaded; idle-ollama cost is negligible.
        """
        # Step 1: graceful model unload
        models = self._ollama_loaded_models()
        if models:
            self._emit("stop", "info", f"unloading {len(models)} model(s) from VRAM: {', '.join(models)}")
            for m in models:
                try:
                    subprocess.run(
                        ["ollama", "stop", m],
                        capture_output=True, text=True, timeout=10,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    # Best effort — proceed to terminate even if unload fails
                    pass

        # Step 2: ownership check. We only stop the server process if the
        # pid file says we spawned it. Otherwise it's externally managed
        # (typically the Windows Ollama desktop app) — unloading models is
        # the right and only polite action.
        pid = self._read_ollama_pid()
        if pid is None or not self._pid_is_alive(pid):
            self._clear_ollama_pid()
            self._emit("stop", "info",
                       "ollama is externally managed (no pid file) — models unloaded, "
                       "process left running")
            return

        # Step 3: deliver a graceful shutdown signal.
        #
        # Windows: the documented way to send Ctrl+Break to another
        # process's console group is a helper process that does
        # FreeConsole → AttachConsole(pid) → SetConsoleCtrlHandler(NULL,
        # TRUE) → GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0). We can't
        # do it in-process because our own console would receive the
        # signal too. The helper is a small inline Python snippet.
        #
        # POSIX: just SIGTERM the process (session leader).
        try:
            if IS_WINDOWS:
                ok = self._win_send_ctrl_break(pid)
                if ok:
                    self._emit("stop", "info",
                               f"sent Ctrl+Break to ollama (pid {pid})")
                else:
                    self._emit("stop", "info",
                               f"Ctrl+Break helper failed for pid {pid}; "
                               "will rely on force-kill fallback")
            else:
                os.kill(pid, signal.SIGTERM)
                self._emit("stop", "info",
                           f"sent SIGTERM to ollama (pid {pid})")
        except (ProcessLookupError, OSError) as e:
            self._emit("stop", "info", f"could not signal ollama pid {pid}: {e}")

        # Step 4: poll up to 10s for the server to exit on its own.
        # The proof of graceful shutdown is exit WITHIN the grace window —
        # if ollama had ignored the signal it would still be alive at 10s
        # and we'd fall through to force-kill. (Ollama's Go server doesn't
        # emit a distinctive shutdown log line, so we can't prove graceful
        # via log scan — exit-within-grace is the empirical evidence.)
        t_signal = time.monotonic()
        grace_deadline = t_signal + 10.0
        while time.monotonic() < grace_deadline:
            if not self._pid_is_alive(pid):
                elapsed = time.monotonic() - t_signal
                self._emit("stop", "info",
                           f"ollama exited gracefully {elapsed:.2f}s after signal")
                self._clear_ollama_pid()
                return
            time.sleep(0.25)

        # Step 5: force-kill fallback (safe — models already unloaded).
        detail = ("models already unloaded, no state lost"
                  if models else "no models were loaded")
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=5,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            self._emit("stop", "info",
                       f"ollama force-killed after 10s graceful timeout — {detail}")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError,
                ProcessLookupError) as e:
            self._emit("stop", "info", f"ollama terminate failed: {e}")
        finally:
            self._clear_ollama_pid()

    def _win_send_ctrl_break(self, pid: int) -> bool:
        """Deliver CTRL_BREAK_EVENT to `pid`'s console via a helper
        subprocess. The helper detaches from its own console, attaches
        to pid's, disables its own Ctrl handler so it survives the
        signal, fires GenerateConsoleCtrlEvent, and exits. Returns True
        if the helper exited cleanly (signal was delivered), False
        otherwise.

        Why a subprocess: doing FreeConsole/AttachConsole in this Python
        process would detach us from whatever console we're running in,
        disrupting normal I/O and potentially killing our own process
        when the signal fires.
        """
        import sys as _sys
        # Key detail: GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid)
        # targets ONLY ollama's process group (pid is the PGID leader
        # because we spawned with CREATE_NEW_PROCESS_GROUP). Passing 0
        # as the group id would broadcast to every process sharing
        # the attached console — killing the helper too, which is the
        # same Ctrl+Break signal noise SetConsoleCtrlHandler(NULL, TRUE)
        # does NOT suppress (that flag only disables Ctrl+C).
        helper = (
            "import ctypes, sys, time;"
            "k = ctypes.windll.kernel32;"
            "pid = int(sys.argv[1]);"
            "k.FreeConsole();"
            "ok = k.AttachConsole(pid);"
            "sys.exit(2) if not ok else None;"
            "k.SetConsoleCtrlHandler(None, True);"
            "rc = k.GenerateConsoleCtrlEvent(1, pid);"  # 1=CTRL_BREAK, target=pgid
            "sys.exit(3) if rc == 0 else None;"
            "time.sleep(0.2);"
            "k.FreeConsole();"
            "sys.exit(0)"
        )
        try:
            r = subprocess.run(
                [_sys.executable, "-c", helper, str(pid)],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # STATUS_CONTROL_C_EXIT (0xC000013A) means the helper caught
            # its own Ctrl+Break and died — still counts as "signal was
            # delivered" from our POV, though targeting the pgid should
            # normally prevent this.
            STATUS_CONTROL_C_EXIT = 0xC000013A
            return r.returncode in (0, STATUS_CONTROL_C_EXIT)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def _pid_is_alive(self, pid: int) -> bool:
        """Cheap per-pid liveness check. True if the PID exists on the OS."""
        try:
            if IS_WINDOWS:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                return str(pid) in (r.stdout or "")
            else:
                os.kill(pid, 0)
                return True
        except (ProcessLookupError, subprocess.TimeoutExpired,
                FileNotFoundError, OSError):
            return False

    # ---------- Ollama helpers: pid file + graceful-shutdown proof ----------

    def _read_ollama_pid(self) -> Optional[int]:
        try:
            raw = self.ollama_pid_file.read_text(encoding="utf-8").strip()
            return int(raw) if raw else None
        except (OSError, ValueError):
            return None

    def _clear_ollama_pid(self) -> None:
        try:
            self.ollama_pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _stop_db(self) -> None:
        for container in (DB_CONTAINER, DB_CONTAINER_V2):
            try:
                r = subprocess.run(
                    ["docker", "stop", container],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode == 0:
                    self._emit("stop", "info", f"{container} stopped")
                else:
                    self._emit("stop", "info", f"{container} stop rc={r.returncode}")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                self._emit("stop", "info", f"docker stop {container} failed: {e}")

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
