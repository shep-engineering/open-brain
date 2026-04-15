# Dashboard

Open Brain ships with a dark-themed monitoring dashboard built on customtkinter. It shows memory stats, service health, recent captures, most-accessed memories, and OpenTelemetry observability metrics.

---

## Features

| Panel | What it shows |
|-------|---------------|
| **Stat Cards** | Total memories, +1 hour, +24 hours, pinned, upvoted, accessed |
| **Service Pills** | PostgreSQL, Ollama, MCP Server status (green/red/yellow). One-click Start button when a service is down. |
| **Titlebar Dots** | Per-service colored dots at a glance |
| **GPU Selector** *(v0.10.0+)* | Dropdown auto-populated from `nvidia-smi -L` showing the available CUDA devices ("RTX 5090", "RTX 3080 Ti", "Auto"). Hidden if `nvidia-smi` reports no GPUs. Apply button does a graceful Ollama restart so the new selection takes effect. |
| **By Type / By Project** | Bar charts showing memory distribution |
| **Recent Memories** | Last 10 memories with clickable rows to read full content |
| **Most Accessed** | Top 10 most-recalled memories (quality signal) |
| **Observability Strip** | Tool call counts, error rate, top tools, last restart. Reads from OpenTelemetry traces. |
| **Server Log** | Live tail of OTel trace log + Ollama log |

---

## Event-Driven Refresh

The dashboard does **not** poll on a timer. A PostgreSQL trigger fires `pg_notify('memories_changed')` on every INSERT, UPDATE, or DELETE to the `memories` table. The dashboard listens on a dedicated connection and refreshes data widgets instantly when something changes.

Service health checks (Ollama, MCP) run on a separate 60-second interval since they don't need to be instant.

This eliminates the "strobe" effect that timer-based polling causes.

---

## Launching

The dashboard detects whether Open Brain is already running. If services are up, it opens directly. If not, it shows a startup splash that drives `scripts/infrastructure.py`'s `bring_up()` in a worker thread — bringing up Docker, the `open-brain-db` container, and `ollama serve` in sequence while streaming structured progress to the splash and to `logs/startup.log`. Since v0.8.0 this is a pure-Python flow (no `.cmd` chain, no pipe inheritance); since v0.9.0 shutdown uses a real Win32 Ctrl+Break graceful signal for ollama instead of `taskkill /F`; since v0.10.0 a single-instance guard inside `dashboard.py` itself prevents a duplicate window when the desktop shortcut is double-clicked twice — it focuses the existing window instead of spawning a second one.

=== "Desktop Shortcut (Windows)"

    Double-click **Open Brain Dashboard** on your Desktop. To create shortcuts:

    ```powershell
    powershell -ExecutionPolicy Bypass -File scripts\windows\create-desktop-shortcuts.ps1
    ```

=== "Command Line"

    ```sh
    # Windows (no console window)
    .venv\Scripts\pythonw.exe dashboard.py

    # Windows (with console for debugging)
    .venv\Scripts\python.exe dashboard.py
    ```

---

## Restart MCP

The titlebar has a **Restart MCP** button that:

1. Kills the existing `server.py` process (via WSL `pkill`)
2. Polls until the process is confirmed dead
3. Launches a new server in a `tmux` session
4. Polls until the process is confirmed alive
5. Shows step-by-step progress in the status bar

---

## Memory Popup

Click any row in the Recent Memories or Most Accessed tables to open a popup showing the full memory content, type, project, and timestamp.

---

## Observability

The dashboard reads OpenTelemetry spans from `logs/otel-traces.jsonl`. Every MCP tool call produces a span with:

- `mcp.tool` attribute (tool name)
- `status` (OK or ERROR)
- `duration_ms` (call latency)
- `trace_id` and `span_id` for correlation

The observability strip aggregates these into: total calls, total errors, error rate %, top 3 tools by call count, and last server restart timestamp.

The dashboard itself writes its own spans to the same file (service name `open-brain-dashboard`) so refresh cycles are also traceable.

---

## Requirements

The dashboard requires two additional Python packages (both included in `requirements.txt`):

- `customtkinter` -- dark-themed Tk widgets
- `pillow` -- image handling for the app icon

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard hangs on launch | Check that PostgreSQL is reachable. The dashboard uses `127.0.0.1` (not `localhost`) to avoid DNS resolution issues on Windows. |
| Taskbar shows generic Python icon | Reboot Windows to flush the icon cache. The dashboard sets `SetCurrentProcessExplicitAppUserModelID` and DPI awareness. |
| Observability strip shows zeros | Make some MCP tool calls (search, remember, etc.) to generate OTel spans in `logs/otel-traces.jsonl`. |
| MCP status shows offline | If the server was active within the last 5 minutes, it should show online. Check that `logs/otel-traces.jsonl` has recent spans with `"service": "open-brain"`. |
