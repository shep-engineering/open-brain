#!/usr/bin/env python3
"""
Open Brain Dashboard - dark windowed GUI (customtkinter)
Usage: F:\open-brain\.venv\Scripts\python.exe dashboard.py
"""

import json
import os
import sys
import time
import threading
import subprocess
from datetime import datetime
from pathlib import Path

import psycopg2
import customtkinter as ctk

BASE_DIR = Path(__file__).parent.resolve()
ON_SCRIPT = BASE_DIR / "scripts" / "windows" / "open-brain-on.cmd"
ON_SCRIPT_SH = BASE_DIR / "scripts" / "open-brain-on.sh"
IS_WINDOWS = sys.platform == "win32"

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@127.0.0.1:5432/openbrain")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG     = "#0e0e12"
PANEL  = "#15151e"
BORDER = "#2a2a38"
PURPLE = "#a78bfa"
GREEN  = "#4ade80"
RED    = "#f87171"
YELLOW = "#facc15"
DIM    = "#6b6b80"
WHITE  = "#f0f0ff"


OBS_LOG    = BASE_DIR / "logs" / "open-brain.jsonl"
OLLAMA_LOG = BASE_DIR / "logs" / "ollama.log"
OTEL_LOG   = BASE_DIR / "logs" / "otel-traces.jsonl"
STARTUP_LOG = BASE_DIR / "logs" / "startup.log"

# Initialize dashboard telemetry (own spans for refresh cycles)
# Dashboard only needs the JSONL file exporter: skip OTLP (no collector) and
# ConsoleSpanExporter (pythonw.exe has no console, would block or error).
_dash_tracer = None
try:
    import os as _os
    _os.environ.setdefault("OTEL_SERVICE_NAME", "open-brain-dashboard")
    from opentelemetry import trace as _otrace
    from opentelemetry.sdk.trace import TracerProvider as _TP
    from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BSP
    from opentelemetry.sdk.resources import Resource as _Res
    from telemetry import _JSONLSpanExporter

    _dash_res = _Res.create({"service.name": "open-brain-dashboard"})
    _dash_tp = _TP(resource=_dash_res)
    _dash_tp.add_span_processor(
        _BSP(_JSONLSpanExporter(BASE_DIR / "logs" / "otel-traces.jsonl"))
    )
    _otrace.set_tracer_provider(_dash_tp)
    _dash_tracer = _otrace.get_tracer("open-brain-dashboard")
except Exception:
    _dash_tracer = None

# Suppress cmd window popups on Windows for all subprocess calls
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


def _tail_server_log(lines: int = 12):
    """Return (lines_list, source_label) combining OTel traces + Ollama logs."""
    results = []
    sources = []

    # 1. OTel traces JSONL (primary — every span from server + dashboard)
    if OTEL_LOG.exists():
        try:
            raw = OTEL_LOG.read_text(errors="replace").splitlines()
            for line in raw[-80:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    attrs    = e.get("attrs") or {}
                    # Only show MCP tool calls, not DB queries or uptime heartbeats
                    tool = attrs.get("mcp.tool")
                    span_name = e.get("span", "")
                    if not tool and span_name in ("SELECT", "UPDATE", "INSERT", "DELETE", "CREATE"):
                        continue
                    ts       = e.get("ts", "")
                    svc      = e.get("service", "")[:12].ljust(12)
                    span     = span_name[:28].ljust(28)
                    status   = e.get("status", "")
                    dur      = e.get("duration_ms", "")
                    tid      = e.get("trace_id", "")[-8:]
                    caller   = attrs.get("mcp.source", "")
                    caller_tag = f" [{caller}]" if caller else ""
                    ts_short = ts[11:23] if len(ts) > 11 else ts
                    status_tag = " ERR" if status == "ERROR" else ""
                    results.append((ts, f"[{svc}] {ts_short} {span} {dur}ms t:{tid}{caller_tag}{status_tag}"))
                except Exception:
                    results.append(("", f"[otel]  {line[:100]}"))
            sources.append("otel")
        except Exception:
            pass

    # 2. Legacy MCP observability JSONL (fallback if otel not yet populated)
    if not sources and OBS_LOG.exists():
        try:
            raw = OBS_LOG.read_text(errors="replace").splitlines()
            for line in raw[-50:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    ts  = e.get("ts", "")
                    lvl = e.get("level", "INFO")[:5].ljust(5)
                    evt = e.get("event", "")[:22].ljust(22)
                    extra = ""
                    if e.get("tool"):         extra += f" {e['tool']}"
                    if e.get("duration_ms"):  extra += f" {e['duration_ms']:.0f}ms"
                    if e.get("error"):        extra += f" ERR:{e['error'][:35]}"
                    ts_short = ts[11:23] if len(ts) > 11 else ts
                    results.append((ts, f"[MCP]    {ts_short} {lvl} {evt}{extra}"))
                except Exception:
                    results.append(("", f"[MCP]    {line[:100]}"))
            sources.append("mcp-legacy")
        except Exception:
            pass

    # 2. Ollama log — only include if recently modified (within 1 hour)
    if OLLAMA_LOG.exists():
        try:
            import os as _os
            age_seconds = time.time() - _os.path.getmtime(str(OLLAMA_LOG))
            if age_seconds < 3600:  # only if modified in last hour
                raw = OLLAMA_LOG.read_text(errors="replace").splitlines()
                for line in raw[-50:]:
                    line = line.strip()
                    if not line:
                        continue
                    ts = ""
                    if line.startswith("time="):
                        try:
                            ts = line.split(" ")[0].replace("time=", "")
                        except Exception:
                            pass
                    elif line.startswith("[GIN]"):
                        try:
                            parts = line.split()
                            ts = f"{parts[1]}T{parts[3]}"
                        except Exception:
                            pass
                    results.append((ts, f"[Ollama] {line[:100]}"))
                sources.append("ollama")
        except Exception:
            pass

    if results:
        # Sort by timestamp where available, keep insertion order otherwise
        results.sort(key=lambda x: x[0] if x[0] else "")
        lines_out = [r[1] for r in results[-lines:]]
        return lines_out, " + ".join(sources)

    # 3. Fallback: server-crash.log
    crash_log = BASE_DIR / "server-crash.log"
    if crash_log.exists():
        try:
            text = crash_log.read_text(errors="replace").splitlines()
            nonempty = [l for l in text if l.strip()]
            return nonempty[-lines:], "server-crash.log"
        except Exception:
            pass

    return ["(no log data — restart Open Brain to populate)"], "none"


def fetch_obs_metrics() -> dict:
    """Parse OTel traces and return aggregated metrics for the dashboard."""
    counts: dict = {}
    errors: dict = {}
    times:  dict = {}
    last_startup = None

    # Tool call metrics from OTel traces
    if OTEL_LOG.exists():
        try:
            with open(OTEL_LOG, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    attrs = e.get("attrs") or {}
                    tool = attrs.get("mcp.tool")
                    if not tool:
                        continue
                    counts[tool] = counts.get(tool, 0) + 1
                    if e.get("status") == "ERROR":
                        errors[tool] = errors.get(tool, 0) + 1
                    ms = e.get("duration_ms")
                    if ms is not None:
                        times.setdefault(tool, []).append(ms)
        except Exception:
            pass

    # Last startup: use the earliest OTel trace timestamp from the current day
    # as a proxy (the server writes traces on every tool call)
    if OTEL_LOG.exists() and not last_startup:
        try:
            with open(OTEL_LOG, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("service") == "open-brain":
                        ts = e.get("ts", "")
                        if ts:
                            last_startup = ts
                            break  # first trace = earliest startup
        except Exception:
            pass

    total = sum(counts.values())
    errtotal = sum(errors.values())
    avg_ms = {t: round(sum(v)/len(v)) for t, v in times.items() if v}
    top5 = sorted(counts.items(), key=lambda x: -x[1])[:5]
    return {
        "total_calls":  total,
        "total_errors": errtotal,
        "error_rate":   round(errtotal/total*100, 1) if total else 0.0,
        "top_tools":    top5,
        "avg_ms":       avg_ms,
        "last_startup": last_startup,
    }


def is_db_up() -> bool:
    """Quick check: can we connect to postgres?"""
    try:
        conn = psycopg2.connect(DB_URL, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


def launch_open_brain():
    """Fire the Open Brain ON script, capturing stdout for the splash screen."""
    if IS_WINDOWS and ON_SCRIPT.exists():
        return subprocess.Popen(
            ["cmd", "/c", str(ON_SCRIPT)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    elif ON_SCRIPT_SH.exists():
        return subprocess.Popen(
            ["bash", str(ON_SCRIPT_SH)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    return None


def db_connect():
    return psycopg2.connect(DB_URL)


def fetch_stats():
    try:
        conn = db_connect()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM memories")
        total = cur.fetchone()[0]

        cur.execute("SELECT metadata->>'type', COUNT(*) FROM memories GROUP BY 1 ORDER BY 2 DESC")
        by_type = cur.fetchall()

        cur.execute(
            "SELECT project, COUNT(*) FROM memories "
            "WHERE project IS NOT NULL AND project != '' "
            "GROUP BY project ORDER BY 2 DESC LIMIT 8"
        )
        by_project = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM memories WHERE pinned = true")
        pinned = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM memories WHERE upvotes > 0")
        upvoted = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM memories WHERE access_count > 0")
        accessed = cur.fetchone()[0]

        cur.execute(
            "SELECT content, metadata->>'type', project, created_at "
            "FROM memories ORDER BY created_at DESC LIMIT 10"
        )
        recent = cur.fetchall()

        cur.execute(
            "SELECT content, access_count, metadata->>'type', project "
            "FROM memories WHERE access_count > 0 "
            "ORDER BY access_count DESC LIMIT 10"
        )
        hot = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM memories WHERE created_at > NOW() - INTERVAL '1 hour'")
        last_hour = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM memories WHERE created_at > NOW() - INTERVAL '24 hours'")
        last_day = cur.fetchone()[0]

        conn.close()
        return dict(
            total=total, by_type=by_type, by_project=by_project,
            pinned=pinned, upvoted=upvoted, accessed=accessed,
            recent=recent, hot=hot, last_hour=last_hour, last_day=last_day,
            error=None,
        )
    except Exception as e:
        return dict(error=str(e))


def check_ollama():
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
        return "online"
    except Exception:
        return "offline"


def check_mcp():
    """Check if Open Brain MCP server process is running (cross-platform)."""
    from datetime import timezone

    # Check 1: Recent OTel traces from the MCP server (most reliable —
    # works regardless of how the server was started: Windows native, WSL, MCP client stdio)
    if OTEL_LOG.exists():
        try:
            with open(OTEL_LOG, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode("utf-8", errors="replace")
            for line in reversed(tail.strip().splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("service") != "open-brain":
                    continue
                ts = e.get("ts", "")
                if ts:
                    try:
                        dt = datetime.fromisoformat(ts)
                        age = (datetime.now(timezone.utc) - dt).total_seconds()
                        if age < 300:  # active within 5 minutes
                            return "online"
                    except Exception:
                        pass
                break  # only check the most recent open-brain span
        except Exception:
            pass

    # Check 2: Socket check (works if REST API is running on port 8765)
    try:
        import socket
        s = socket.socket()
        s.settimeout(1)
        s.connect(("127.0.0.1", 8765))
        s.close()
        return "online"
    except Exception:
        pass

    # Check 3: Windows process check -- look for python.exe running server.py
    if IS_WINDOWS:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
                creationflags=_NO_WINDOW,
            )
            # Check if any python process has server.py in its command line
            r2 = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
                capture_output=True, text=True, timeout=3,
                creationflags=_NO_WINDOW,
            )
            if "server.py" in r2.stdout:
                return "online"
        except Exception:
            pass

    return "offline"


class Dashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Open Brain Dashboard")
        self.geometry("1200x920")
        self.minsize(1000, 860)
        self.configure(fg_color=BG)
        self.resizable(True, True)
        _ico = BASE_DIR / "assets" / "brain.ico"
        if _ico.exists():
            self.iconbitmap(str(_ico))
            # iconbitmap gives a blurry taskbar icon; iconphoto passes full-res data
            try:
                from PIL import Image, ImageTk
                _img = Image.open(str(_ico))
                _img = _img.resize((256, 256), Image.LANCZOS)
                self._icon_photo = ImageTk.PhotoImage(_img)
                self.iconphoto(True, self._icon_photo)
            except Exception:
                pass
        self._alive = True
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._setup_notify_trigger()
        self._start_listen_thread()
        self._schedule_service_check()
        self._schedule_fallback_refresh()
        # Initial data load
        threading.Thread(target=self._fetch_and_update, daemon=True).start()
        self._tick_clock()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_titlebar()
        self._build_stat_cards()
        self._build_service_pills()
        self._build_obs_strip()
        self._build_charts()
        self._build_tables()
        self._build_log_panel()

    def _build_titlebar(self):
        bar = ctk.CTkFrame(self, fg_color="#1a1a24", corner_radius=0, height=48)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        ctk.CTkLabel(
            bar, text="⬡  Open Brain", font=("Segoe UI", 18, "bold"), text_color=PURPLE
        ).pack(side="left", padx=16, pady=10)

        # Individual colored dot + label per service
        self._title_dots = {}
        for svc, abbr in (("PostgreSQL", "DB"), ("Ollama", "Ollama"), ("MCP Server", "MCP")):
            dot = ctk.CTkLabel(bar, text="●", font=("Segoe UI", 12), text_color=BORDER)
            dot.pack(side="left", padx=(8, 1), pady=10)
            ctk.CTkLabel(bar, text=abbr, font=("Segoe UI", 12), text_color=DIM).pack(side="left", padx=(0, 4), pady=10)
            self._title_dots[svc] = dot

        # Status message area (used by Restart MCP progress)
        self.status_label = ctk.CTkLabel(bar, text="", font=("Segoe UI", 11), text_color=DIM)
        self.status_label.pack(side="left", padx=12)

        self.clock_label = ctk.CTkLabel(bar, text="", font=("Consolas", 13), text_color=DIM)
        self.clock_label.pack(side="right", padx=16)

        ctk.CTkButton(
            bar, text="↺  Refresh", width=100, height=30,
            fg_color=PANEL, hover_color=BORDER, text_color=PURPLE, corner_radius=8,
            command=self._manual_refresh,
        ).pack(side="right", padx=8, pady=9)

        ctk.CTkButton(
            bar, text="⟳  Restart MCP", width=120, height=30,
            fg_color=PANEL, hover_color="#2a1a3a", text_color=YELLOW, corner_radius=8,
            command=self._restart_mcp,
        ).pack(side="right", padx=0, pady=9)

    def _build_stat_cards(self):
        row = ctk.CTkFrame(self, fg_color=BG)
        row.pack(fill="x", padx=12, pady=(10, 0))

        self.stat_cards = {}
        defs = [
            ("total",     "Total Memories", WHITE),
            ("last_hour", "+1 Hour",        GREEN),
            ("last_day",  "+24 Hours",      GREEN),
            ("pinned",    "Pinned",         YELLOW),
            ("upvoted",   "Upvoted",        YELLOW),
            ("accessed",  "Accessed",       WHITE),
        ]
        for key, label, color in defs:
            card = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=10,
                                border_width=1, border_color=BORDER)
            card.pack(side="left", expand=True, fill="x", padx=5)
            ctk.CTkLabel(card, text=label, font=("Segoe UI", 11),
                         text_color=DIM).pack(pady=(12, 0))
            val = ctk.CTkLabel(card, text="…", font=("Segoe UI", 28, "bold"),
                               text_color=BORDER)  # dim loading placeholder
            val.pack(pady=(2, 12))
            self.stat_cards[key] = val

    def _build_service_pills(self):
        row = ctk.CTkFrame(self, fg_color=BG)
        row.pack(fill="x", padx=12, pady=8)

        self.svc_dots = {}
        self.svc_btns = {}
        for svc in ("PostgreSQL", "Ollama", "MCP Server"):
            pill = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=8,
                                border_width=1, border_color=BORDER)
            pill.pack(side="left", padx=6, pady=2)
            dot = ctk.CTkLabel(pill, text="◌", font=("Segoe UI", 14), text_color=BORDER)  # loading ring
            dot.pack(side="left", padx=(12, 4), pady=6)
            ctk.CTkLabel(pill, text=svc, font=("Segoe UI", 12),
                         text_color=DIM).pack(side="left", padx=(0, 6), pady=6)
            btn = ctk.CTkButton(pill, text="Start", width=52, height=22,
                                fg_color="#1a2a1a", hover_color=GREEN,
                                text_color=GREEN, corner_radius=5,
                                font=("Segoe UI", 10),
                                command=lambda s=svc: self._start_service(s))
            btn.pack(side="left", padx=(0, 10), pady=4)
            btn.pack_forget()  # hidden until service is down
            self.svc_dots[svc] = dot
            self.svc_btns[svc] = btn

    def _build_obs_strip(self):
        """Thin observability metrics bar: calls, errors, error rate, top tool, last startup."""
        strip = ctk.CTkFrame(self, fg_color="#0e0e18", corner_radius=0, height=30)
        strip.pack(fill="x", padx=0, pady=0)
        strip.pack_propagate(False)
        self._obs_label = ctk.CTkLabel(
            strip, text="  Observability: fetching metrics...",
            font=("Consolas", 11), text_color=BORDER, anchor="w",
        )
        self._obs_label.pack(side="left", padx=12, fill="x")

    def _build_charts(self):
        row = ctk.CTkFrame(self, fg_color=BG)
        row.pack(fill="x", padx=12, pady=0)

        type_panel = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=10,
                                  border_width=1, border_color=BORDER)
        type_panel.pack(side="left", expand=True, fill="both", padx=(0, 6), pady=4)
        ctk.CTkLabel(type_panel, text="By Type", font=("Segoe UI", 13, "bold"),
                     text_color=PURPLE).pack(anchor="w", padx=14, pady=(10, 4))
        self.type_box = ctk.CTkTextbox(type_panel, fg_color=PANEL, text_color=WHITE,
                                       font=("Consolas", 12), height=140, border_width=0)
        self.type_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._set_text(self.type_box, "  loading...")

        proj_panel = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=10,
                                  border_width=1, border_color=BORDER)
        proj_panel.pack(side="left", expand=True, fill="both", padx=(6, 0), pady=4)
        ctk.CTkLabel(proj_panel, text="By Project", font=("Segoe UI", 13, "bold"),
                     text_color=PURPLE).pack(anchor="w", padx=14, pady=(10, 4))
        self.proj_box = ctk.CTkTextbox(proj_panel, fg_color=PANEL, text_color=WHITE,
                                       font=("Consolas", 12), height=140, border_width=0)
        self.proj_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._set_text(self.proj_box, "  loading...")

    def _build_tables(self):
        row = ctk.CTkFrame(self, fg_color=BG)
        row.pack(fill="both", expand=True, padx=12, pady=0)

        # Recent
        recent_panel = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=10,
                                    border_width=1, border_color=BORDER)
        recent_panel.pack(side="left", expand=True, fill="both", padx=(0, 6), pady=4)
        ctk.CTkLabel(recent_panel, text="Recent Memories  ", font=("Segoe UI", 13, "bold"),
                     text_color=PURPLE).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(recent_panel, text="click a row to read", font=("Segoe UI", 10),
                     text_color=DIM).pack(anchor="w", padx=14, pady=(0, 4))
        self.recent_scroll = ctk.CTkScrollableFrame(recent_panel, fg_color=PANEL,
                                                    scrollbar_button_color=BORDER)
        self.recent_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        ctk.CTkLabel(self.recent_scroll, text="Loading memories...",
                     font=("Segoe UI", 11), text_color=BORDER).pack(pady=20)

        # Hot
        hot_panel = ctk.CTkFrame(row, fg_color=PANEL, corner_radius=10,
                                 border_width=1, border_color=BORDER)
        hot_panel.pack(side="left", expand=True, fill="both", padx=(6, 0), pady=4)
        ctk.CTkLabel(hot_panel, text="Most Accessed  ", font=("Segoe UI", 13, "bold"),
                     text_color=PURPLE).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(hot_panel, text="click a row to read", font=("Segoe UI", 10),
                     text_color=DIM).pack(anchor="w", padx=14, pady=(0, 4))
        self.hot_scroll = ctk.CTkScrollableFrame(hot_panel, fg_color=PANEL,
                                                 scrollbar_button_color=BORDER)
        self.hot_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        ctk.CTkLabel(self.hot_scroll, text="Loading memories...",
                     font=("Segoe UI", 11), text_color=BORDER).pack(pady=20)

    def _build_log_panel(self):
        panel = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10,
                             border_width=1, border_color=BORDER, height=110)
        panel.pack(fill="x", padx=12, pady=(0, 8))
        panel.pack_propagate(False)

        hdr = ctk.CTkFrame(panel, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(8, 0))
        ctk.CTkLabel(hdr, text="Server Log", font=("Segoe UI", 13, "bold"),
                     text_color=PURPLE).pack(side="left")
        self._log_source_label = ctk.CTkLabel(hdr, text="", font=("Segoe UI", 10),
                                              text_color=DIM)
        self._log_source_label.pack(side="left", padx=8)

        self.log_box = ctk.CTkTextbox(panel, fg_color=PANEL, text_color="#88ffcc",
                                      font=("Consolas", 10), border_width=0, height=90)
        self.log_box.pack(fill="x", padx=10, pady=(2, 8))
        self._set_text(self.log_box, "  waiting for logs...")

    # ── Data refresh (event-driven via PostgreSQL LISTEN/NOTIFY) ─────────────

    def _setup_notify_trigger(self):
        """Ensure the NOTIFY trigger exists on the memories table."""
        try:
            conn = db_connect()
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("""
                CREATE OR REPLACE FUNCTION notify_dashboard()
                RETURNS trigger AS $$
                BEGIN
                    PERFORM pg_notify('memories_changed', TG_OP);
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """)
            cur.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger WHERE tgname = 'memories_notify'
                    ) THEN
                        CREATE TRIGGER memories_notify
                        AFTER INSERT OR UPDATE OR DELETE ON memories
                        FOR EACH STATEMENT EXECUTE FUNCTION notify_dashboard();
                    END IF;
                END $$;
            """)
            conn.close()
        except Exception:
            pass

    def _start_listen_thread(self):
        """Background thread that polls PostgreSQL LISTEN and pokes the GUI on changes."""
        import select as _sel
        def _listen_loop():
            while self._alive:
                conn = None
                try:
                    conn = db_connect()
                    conn.set_isolation_level(0)  # autocommit required for LISTEN
                    cur = conn.cursor()
                    cur.execute("LISTEN memories_changed")
                    keepalive_counter = 0
                    while self._alive:
                        # Use select() to properly wait for notifications (5s timeout)
                        if _sel.select([conn], [], [], 5) != ([], [], []):
                            conn.poll()
                            if conn.notifies:
                                conn.notifies.clear()
                                if self._alive:
                                    self.after(0, self._on_db_changed)
                        # Keepalive: every 60 iterations (~5 min), ping the connection
                        keepalive_counter += 1
                        if keepalive_counter >= 60:
                            keepalive_counter = 0
                            cur.execute("SELECT 1")
                except Exception:
                    if not self._alive:
                        break
                    try:
                        if conn:
                            conn.close()
                    except Exception:
                        pass
                    time.sleep(3)  # reconnect backoff
        threading.Thread(target=_listen_loop, daemon=True).start()

    def _on_db_changed(self):
        """Called on the GUI thread when the DB signals a change."""
        threading.Thread(target=self._fetch_data_only, daemon=True).start()

    def _schedule_service_check(self):
        """Slow poll for service health (Ollama, MCP) — no DB strobe."""
        if not self._alive:
            return
        threading.Thread(target=self._check_services, daemon=True).start()
        self.after(30000, self._schedule_service_check)

    def _check_services(self):
        """Check Ollama + MCP status and update the UI."""
        ollama = check_ollama()
        mcp = check_mcp()
        obs = fetch_obs_metrics()
        if self._alive:
            self.after(0, lambda: self._apply_services(ollama, mcp, obs))

    def _schedule_fallback_refresh(self):
        """Fallback periodic refresh every 30s in case LISTEN/NOTIFY drops."""
        if not self._alive:
            return
        threading.Thread(target=self._fetch_and_update, daemon=True).start()
        self.after(30000, self._schedule_fallback_refresh)

    def _fetch_data_only(self):
        """Fetch DB stats and apply to UI (no service checks — triggered by NOTIFY)."""
        stats = fetch_stats()
        if self._alive:
            self.after(0, lambda: self._apply_data(stats))

    def _apply_data(self, stats):
        """Update only the data-driven widgets (cards, charts, tables)."""
        if not self._alive:
            return
        try:
            self._apply_inner_data(stats)
        except Exception:
            pass

    def _apply_services(self, ollama, mcp, obs_metrics):
        """Update only the service status widgets."""
        if not self._alive:
            return
        try:
            self._apply_inner_services(ollama, mcp, obs_metrics)
        except Exception:
            pass

    def _manual_refresh(self):
        threading.Thread(target=self._fetch_and_update, daemon=True).start()

    def _on_close(self):
        """Show shutdown dialog when user clicks X."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Close Dashboard")
        dialog.configure(fg_color=BG)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.bind("<Escape>", lambda e: dialog.destroy())

        ctk.CTkLabel(dialog, text="⬡  Close Open Brain Dashboard?",
                     font=("Segoe UI", 15, "bold"), text_color=WHITE).pack(pady=(30, 6), padx=30)
        ctk.CTkLabel(dialog, text="Would you also like to stop all Open Brain services?",
                     font=("Segoe UI", 11), text_color=DIM).pack(pady=(0, 24), padx=30)

        def just_close():
            self._alive = False
            dialog.destroy()
            self.destroy()

        def close_and_stop():
            self._alive = False
            dialog.destroy()
            self._run_off_script()
            self.destroy()

        W = 340
        ctk.CTkButton(dialog, text="🔴  Close + Stop Open Brain", width=W, height=40,
                      fg_color="#2a1010", hover_color=RED, text_color="#ff6b6b",
                      font=("Segoe UI", 12, "bold"),
                      corner_radius=8, command=close_and_stop).pack(pady=(0, 8), padx=30)
        ctk.CTkButton(dialog, text="Just Close Dashboard", width=W, height=40,
                      fg_color=PANEL, hover_color=BORDER, text_color=WHITE,
                      font=("Segoe UI", 12),
                      corner_radius=8, command=just_close).pack(pady=(0, 8), padx=30)
        ctk.CTkButton(dialog, text="Cancel", width=W, height=36,
                      fg_color="transparent", hover_color=PANEL, text_color=DIM,
                      font=("Segoe UI", 11),
                      corner_radius=8, command=dialog.destroy).pack(pady=(0, 20), padx=30)

        # Defer geometry until after widgets are laid out
        def _center():
            try:
                if not dialog.winfo_exists():
                    return
                dialog.update_idletasks()
                w, h = 400, dialog.winfo_reqheight() + 20
                x = self.winfo_x() + (self.winfo_width()  - w) // 2
                y = self.winfo_y() + (self.winfo_height() - h) // 2
                dialog.geometry(f"{w}x{h}+{x}+{y}")
                dialog.focus_force()
            except Exception:
                pass
        dialog.after(50, _center)

    def _run_off_script(self):
        """Run open-brain-off silently in background."""
        off_script = BASE_DIR / "scripts" / "windows" / "open-brain-off.cmd"
        off_sh     = BASE_DIR / "scripts" / "open-brain-off.sh"
        try:
            if IS_WINDOWS and off_script.exists():
                subprocess.Popen(
                    ["cmd", "/c", str(off_script)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            elif off_sh.exists():
                subprocess.Popen(["bash", str(off_sh)])
        except Exception:
            pass

    def _start_service(self, svc: str):
        """Start a specific service that is down."""
        def _do():
            if svc == "Ollama":
                if IS_WINDOWS:
                    subprocess.Popen(
                        ["cmd", "/c",
                         "set OLLAMA_NUM_GPU=2 && set CUDA_VISIBLE_DEVICES=0,1 && "
                         "set OLLAMA_KEEP_ALIVE=30m && set OLLAMA_MAX_LOADED_MODELS=2 && "
                         f"ollama serve >{BASE_DIR / 'logs' / 'ollama.log'} 2>&1"],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    subprocess.Popen(["ollama", "serve"])
            elif svc == "PostgreSQL":
                subprocess.run(
                    ["docker", "start", "open-brain-db"],
                    capture_output=True, timeout=10,
                    **(dict(creationflags=_NO_WINDOW) if IS_WINDOWS else {}),
                )
            elif svc == "MCP Server":
                self._restart_mcp()
                return
            self.after(3000, self._manual_refresh)
        self.svc_btns[svc].configure(text="...", state="disabled")
        threading.Thread(target=_do, daemon=True).start()

    def _restart_mcp(self):
        """Restart MCP server with real per-step verification and retries. No fake timeouts."""
        self._mcp_restart_btn = None
        for w in self.winfo_children():
            if hasattr(w, 'winfo_children'):
                for b in w.winfo_children():
                    if isinstance(b, ctk.CTkButton) and "Restart" in str(b.cget("text")):
                        b.configure(state="disabled", text="⟳  Restarting...")
                        self._mcp_restart_btn = b

        def _set_status(msg, color=YELLOW):
            if not self._alive:
                return
            try:
                self.status_label.configure(text=msg, text_color=color)
            except Exception:
                pass

        def _find_server_pids():
            """Find PIDs of python.exe processes running server.py (Windows native)."""
            pids = []
            try:
                r = subprocess.run(
                    ["wmic", "process", "where", "name='python.exe'", "get", "processid,commandline"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=_NO_WINDOW,
                )
                for line in r.stdout.splitlines():
                    if "server.py" in line and "dashboard" not in line.lower():
                        parts = line.strip().split()
                        if parts:
                            try:
                                pids.append(int(parts[-1]))
                            except ValueError:
                                pass
            except Exception:
                pass
            return pids

        def _is_running():
            return len(_find_server_pids()) > 0

        def _wait_until_stopped(max_attempts=10):
            for i in range(max_attempts):
                if not _is_running():
                    return True
                self.after(0, lambda i=i: _set_status(f"MCP: waiting for stop... ({i+1}/{max_attempts})"))
                time.sleep(1)
            return False

        def _wait_until_started(max_attempts=15):
            for i in range(max_attempts):
                if _is_running():
                    return True
                self.after(0, lambda i=i: _set_status(f"MCP: waiting for start... ({i+1}/{max_attempts})"))
                time.sleep(1)
            return False

        def _do_restart():
            # ── Step 1: Kill existing server (Windows native) ─────────────────
            self.after(0, lambda: _set_status("MCP: stopping existing server..."))
            pids = _find_server_pids()
            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=5,
                        creationflags=_NO_WINDOW,
                    )
                except Exception:
                    pass

            # ── Step 2: Verify stopped ────────────────────────────────────────
            if pids:
                self.after(0, lambda: _set_status("MCP: confirming stopped..."))
                _wait_until_stopped(max_attempts=8)

            # ── Step 3: Launch new server (Windows native) ────────────────────
            self.after(0, lambda: _set_status("MCP: launching server.py..."))
            try:
                server_py = str(BASE_DIR / "server.py")
                python_exe = str(BASE_DIR / ".venv" / "Scripts" / "python.exe")
                crash_log = str(BASE_DIR / "server-crash.log")
                subprocess.Popen(
                    [python_exe, server_py],
                    stdout=open(crash_log, "a"),
                    stderr=subprocess.STDOUT,
                    cwd=str(BASE_DIR),
                    creationflags=_NO_WINDOW,
                )
            except Exception as exc:
                self.after(0, lambda: _set_status(f"Launch failed: {exc}", RED))
                _finish(success=False)
                return

            # ── Step 4: Verify started ────────────────────────────────────────
            self.after(0, lambda: _set_status("MCP: verifying server started..."))
            started = _wait_until_started(max_attempts=15)
            if started:
                self.after(0, lambda: _set_status("MCP server running", GREEN))
                _finish(success=True)
            else:
                self.after(0, lambda: _set_status("MCP: failed to start -- check server-crash.log", RED))
                _finish(success=False)

        def _finish(success: bool):
            self.after(0, self._manual_refresh)
            def _re_enable():
                if not self._alive:
                    return
                try:
                    if self._mcp_restart_btn:
                        self._mcp_restart_btn.configure(state="normal", text="⟳  Restart MCP")
                except Exception:
                    pass
                if success:
                    # Clear success message after 8s
                    self.after(8000, lambda: _set_status("", WHITE))
            self.after(0, _re_enable)

        threading.Thread(target=_do_restart, daemon=True).start()

    def _tick_clock(self):
        if not self._alive:
            return
        try:
            self.clock_label.configure(text=datetime.now().strftime("%H:%M:%S"))
        except Exception:
            return
        self.after(1000, self._tick_clock)
        # Refresh log every 2 seconds
        if int(datetime.now().second) % 2 == 0:
            threading.Thread(target=self._refresh_log, daemon=True).start()

    def _refresh_log(self):
        lines, source = _tail_server_log()
        if self._alive:
            self.after(0, lambda: self._apply_log(lines, source))

    def _apply_log(self, lines, source):
        try:
            self._log_source_label.configure(text=source)
            self._set_text(self.log_box, "\n".join(lines))
            self.log_box.see("end")
        except Exception:
            pass

    def _fetch_and_update(self):
        if _dash_tracer:
            with _dash_tracer.start_as_current_span("dashboard.refresh"):
                self._do_fetch()
        else:
            self._do_fetch()

    def _do_fetch(self):
        stats   = fetch_stats()
        ollama  = check_ollama()
        mcp     = check_mcp()
        obs     = fetch_obs_metrics()
        if self._alive:
            self.after(0, lambda: self._apply(stats, ollama, mcp, obs))

    # ── Apply data to widgets ─────────────────────────────────────────────────

    def _apply(self, stats, ollama, mcp, obs_metrics=None):
        if not self._alive:
            return
        try:
            self._apply_inner(stats, ollama, mcp, obs_metrics)
        except Exception:
            pass

    def _update_titlebar_dots(self, db_state, ollama, mcp, color_map):
        """Update individual colored dot labels in the titlebar."""
        for svc, state in (("PostgreSQL", db_state), ("Ollama", ollama), ("MCP Server", mcp)):
            try:
                self._title_dots[svc].configure(text_color=color_map.get(state, BORDER))
            except Exception:
                pass

    def _apply_inner(self, stats, ollama, mcp, obs_metrics=None):
        """Full refresh: services + data (used for initial load and manual refresh)."""
        self._apply_inner_services(ollama, mcp, obs_metrics, stats)
        self._apply_inner_data(stats)

    def _apply_inner_services(self, ollama, mcp, obs_metrics=None, stats=None):
        """Update service pills, titlebar dots, and observability strip only."""
        db_state = "offline" if (stats and stats.get("error")) else "online"
        color_map = {"online": GREEN, "offline": RED, "unknown": YELLOW}

        for svc, state in (("PostgreSQL", db_state), ("Ollama", ollama), ("MCP Server", mcp)):
            dot_color = color_map.get(state, DIM)
            dot_char  = "●" if state == "online" else ("◔" if state == "unknown" else "○")
            self.svc_dots[svc].configure(text=dot_char, text_color=dot_color)
            btn = self.svc_btns[svc]
            if state in ("offline", "unknown"):
                btn.pack(side="left", padx=(0, 10), pady=4)
            else:
                btn.pack_forget()

        self._update_titlebar_dots(db_state, ollama, mcp, color_map)

        if obs_metrics:
            calls   = obs_metrics.get("total_calls", 0)
            errs    = obs_metrics.get("total_errors", 0)
            rate    = obs_metrics.get("error_rate", 0.0)
            top     = obs_metrics.get("top_tools", [])
            startup = (obs_metrics.get("last_startup") or "")[:19].replace("T", " ")
            top_str = "  ".join(f"{t}:{c}" for t, c in top[:3]) if top else "—"
            rate_color = RED if rate > 5 else (YELLOW if rate > 0 else GREEN)
            self._obs_label.configure(
                text=f"  calls: {calls}   errors: {errs}   err%: {rate}   "
                     f"top: {top_str}   last restart: {startup or 'unknown'}",
                text_color=rate_color if errs > 0 else DIM,
            )

    def _apply_inner_data(self, stats):
        """Update stat cards, charts, and tables only (called on DB NOTIFY)."""
        if stats.get("error"):
            for card in self.stat_cards.values():
                card.configure(text="ERR")
            return

        for key in ("total", "last_hour", "last_day", "pinned", "upvoted", "accessed"):
            self.stat_cards[key].configure(text=str(stats.get(key, 0)))

        # Type bar chart — use pipe chars so monospace renders correctly
        by_type = stats.get("by_type") or []
        top = max((c for _, c in by_type), default=1)
        lines = []
        for typ, count in by_type:
            filled = int(count / top * 20)
            bar = "|" * filled + "." * (20 - filled)
            lines.append(f"{(typ or 'unknown'):<13} [{bar}] {count}")
        self._set_text(self.type_box, "\n".join(lines))

        # Project bar chart
        by_proj = stats.get("by_project") or []
        top_p = max((c for _, c in by_proj), default=1)
        lines = []
        for proj, count in by_proj:
            filled = int(count / top_p * 20)
            bar = "|" * filled + "." * (20 - filled)
            lines.append(f"{(proj or 'global')[:15]:<15} [{bar}] {count}")
        self._set_text(self.proj_box, "\n".join(lines))

        # Recent rows
        recent = stats.get("recent") or []
        self._populate_rows(
            self.recent_scroll, recent,
            lambda r: (r[3].astimezone().strftime("%H:%M") if r[3] else "?",
                       r[1] or "note", r[2] or "global", r[0] or ""),
        )

        # Hot rows
        hot = stats.get("hot") or []
        self._populate_rows(
            self.hot_scroll, hot,
            lambda r: (f"{r[1]} hits", r[2] or "note", r[3] or "global", r[0] or ""),
        )

    def _populate_rows(self, scroll_frame, records, row_fn):
        """Clear and repopulate a scrollable frame with clickable memory rows."""
        for w in scroll_frame.winfo_children():
            w.destroy()
        for record in records:
            ts, typ, proj, content = row_fn(record)
            preview = content.splitlines()[0][:60] if content else ""
            row = ctk.CTkFrame(scroll_frame, fg_color="#1a1a28", corner_radius=6,
                               cursor="hand2")
            row.pack(fill="x", pady=2, padx=2)
            # Meta line
            meta = ctk.CTkFrame(row, fg_color="transparent")
            meta.pack(fill="x", padx=10, pady=(6, 0))
            ctk.CTkLabel(meta, text=ts, font=("Consolas", 10), text_color=DIM,
                         width=50, anchor="w").pack(side="left")
            ctk.CTkLabel(meta, text=typ, font=("Segoe UI", 10, "bold"),
                         text_color=PURPLE, width=70, anchor="w").pack(side="left", padx=(6,0))
            ctk.CTkLabel(meta, text=proj, font=("Segoe UI", 10),
                         text_color=DIM, anchor="w").pack(side="left", padx=(6,0))
            # Preview line
            ctk.CTkLabel(row, text=preview, font=("Segoe UI", 11), text_color=WHITE,
                         anchor="w", wraplength=420).pack(fill="x", padx=10, pady=(2, 6))
            # Bind click on all children
            for widget in (row, meta) + tuple(row.winfo_children()) + tuple(meta.winfo_children()):
                widget.bind("<Button-1>", lambda e, c=content, t=typ, p=proj, ts_=ts:
                            self._open_memory_card(c, t, p, ts_))
                widget.bind("<Enter>", lambda e, r=row: r.configure(fg_color="#252535"))
                widget.bind("<Leave>", lambda e, r=row: r.configure(fg_color="#1a1a28"))

    def _open_memory_card(self, content, typ, project, ts):
        """Open a popup showing full memory content using native tkinter Toplevel."""
        import tkinter as tk

        # Debounce: ignore rapid duplicate clicks (CTk fires on parent + child)
        now = time.time()
        if hasattr(self, '_last_popup_time') and now - self._last_popup_time < 0.5:
            return
        self._last_popup_time = now

        display = content.strip() if content and content.strip() else "(no content available)"

        popup = tk.Toplevel(self)
        popup.title("Memory")
        popup.geometry("700x500")
        popup.configure(bg=BG)
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)

        # Header
        hdr = tk.Frame(popup, bg="#1a1a24", height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"{typ}  ·  {project}  ·  {ts}",
                 font=("Segoe UI", 13), fg=PURPLE, bg="#1a1a24").pack(side="left", padx=16, pady=10)
        tk.Button(hdr, text="Close", font=("Segoe UI", 10),
                  fg=WHITE, bg=PANEL, activebackground=RED, bd=0, padx=12, pady=4,
                  command=popup.destroy).pack(side="right", padx=12, pady=8)

        # Content
        txt = tk.Text(popup, bg=PANEL, fg=WHITE, font=("Segoe UI", 13),
                      wrap="word", bd=0, padx=16, pady=16, relief="flat",
                      insertbackground=WHITE, selectbackground=PURPLE)
        txt.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        txt.insert("1.0", display)
        txt.configure(state="disabled")

        popup.focus_force()
        popup.lift()

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


class StartupSplash(ctk.CTkToplevel):
    """Startup splash that streams open-brain-on output live, then opens dashboard."""

    def __init__(self, master, proc, on_ready):
        super().__init__(master)
        self.title("Open Brain — Starting")
        self.geometry("620x420")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # block accidental close during startup

        # Center
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 620) // 2
        y = (self.winfo_screenheight() - 420) // 2
        self.geometry(f"620x420+{x}+{y}")

        ctk.CTkLabel(self, text="⬡  Open Brain", font=("Segoe UI", 20, "bold"),
                     text_color=PURPLE).pack(pady=(20, 4))
        self.msg = ctk.CTkLabel(self, text="Starting services...",
                                font=("Segoe UI", 11), text_color=DIM)
        self.msg.pack(pady=(0, 6))

        self.bar = ctk.CTkProgressBar(self, mode="indeterminate",
                                      progress_color=PURPLE, fg_color=PANEL)
        self.bar.pack(fill="x", padx=40, pady=(0, 8))
        self.bar.start()

        # Live log box
        self.log_box = ctk.CTkTextbox(
            self, fg_color=PANEL, text_color=GREEN,
            font=("Consolas", 11), border_width=0, wrap="word", height=220,
        )
        self.log_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.log_box.configure(state="disabled")

        self._proc = proc
        self._on_ready = on_ready
        threading.Thread(target=self._stream_output, daemon=True).start()

    def _append(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.msg.configure(text=line.strip()[:80] if line.strip() else "Starting...")

    def _stream_output(self):
        """Read stdout from the ON script line by line, then wait for DB."""
        if self._proc and self._proc.stdout:
            for line in self._proc.stdout:
                line = line.rstrip()
                self.after(0, lambda l=line: self._append(l))
            self._proc.wait()

        # Script done — now wait for DB to be reachable
        self.after(0, lambda: self.msg.configure(text="Waiting for database..."))
        for attempt in range(60):
            if is_db_up():
                self.after(0, self._ready)
                return
            self.after(0, lambda a=attempt: self.msg.configure(
                text=f"Waiting for database... ({a + 1}s)"))
            time.sleep(1)
        self.after(0, lambda: self.msg.configure(
            text="Could not connect after 60s. Check Docker."))
        self.after(0, lambda: self.bar.stop())

    def _ready(self):
        self.bar.stop()
        self.destroy()
        self._on_ready()


if __name__ == "__main__":
    import traceback
    log_path = BASE_DIR / "dashboard-crash.log"
    try:
        if IS_WINDOWS:
            import ctypes
            # DPI-aware: render at native resolution so icon + text are crisp
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # Tell Windows this is its own app, not "python.exe" — uses our icon in taskbar
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "openbrain.dashboard"
            )

        # Hidden root window — owns all subsequent windows
        root = ctk.CTk()
        root.withdraw()

        def launch_dashboard():
            app = Dashboard()
            root.destroy()   # kill the hidden root once dashboard is up
            app.mainloop()

        if is_db_up():
            # Open Brain already running — go straight to dashboard
            launch_dashboard()
        else:
            # Not running — launch it, stream output into splash
            proc = launch_open_brain()
            splash = StartupSplash(root, proc, launch_dashboard)
            root.mainloop()  # keeps splash alive until ready

    except Exception:
        with open(log_path, "w") as f:
            import traceback as tb
            tb.print_exc(file=f)
        import traceback as tb
        tb.print_exc()
        input("Press Enter to close...")
