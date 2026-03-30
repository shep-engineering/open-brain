#!/usr/bin/env python3
"""
Open Brain — Agent auto-discovery and wiring.

Scans the system for installed AI agents and injects the open-brain MCP
configuration so every tool can share the same persistent memory.

Usage:
    python server.py wire          # Scan and configure all agents
    python server.py wire --check  # Read-only scan, no changes
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

PYTHON_CMD = os.getenv(
    "OPEN_BRAIN_PYTHON",
    os.path.expanduser("~/.venvs/open-brain/bin/python"),
)
SERVER_PATH = os.getenv(
    "OPEN_BRAIN_SERVER",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py"),
)
MCP_NAME = "open-brain"


def _wsl_to_windows(path: str) -> str:
    """Convert /mnt/x/... WSL path to X:\\... Windows path."""
    if len(path) >= 7 and path[:5] == "/mnt/" and path[6] == "/":
        drive = path[5].upper()
        rest = path[6:].replace("/", "\\")
        return f"{drive}:{rest}"
    return path


def _linux_entry() -> dict:
    return {"command": PYTHON_CMD, "args": [SERVER_PATH]}


def _windows_entry() -> dict:
    """Windows agents run the server using the Windows-side venv Python."""
    server_dir = os.path.dirname(os.path.abspath(SERVER_PATH))
    win_python = os.getenv(
        "OPEN_BRAIN_WIN_PYTHON",
        _wsl_to_windows(os.path.join(server_dir, ".venv", "Scripts", "python.exe")),
    )
    win_server = os.getenv("OPEN_BRAIN_WIN_SERVER", _wsl_to_windows(SERVER_PATH))
    return {"command": win_python, "args": [win_server]}


# ─── Agent Definitions ───────────────────────────────────────────────────────

def _detect_windows_user() -> str | None:
    users_dir = Path("/mnt/c/Users")
    if not users_dir.exists():
        return None
    skip = {"Public", "Default", "Default User", "All Users", "desktop.ini"}
    for d in sorted(users_dir.iterdir()):
        if d.is_dir() and d.name not in skip and (d / "AppData").exists():
            return d.name
    return None


def _get_agents() -> list[dict]:
    """Return known agent config locations to check."""
    home = str(Path.home())
    win_user = _detect_windows_user()
    agents: list[dict] = []

    if win_user:
        wu = f"/mnt/c/Users/{win_user}"
        agents.extend([
            {
                "name": "Claude Desktop",
                "path": f"{wu}/AppData/Roaming/Claude/claude_desktop_config.json",
                "key": "mcpServers",
                "side": "windows",
                "create_ok": True,
            },
            {
                "name": "Cursor",
                "path": f"{wu}/.cursor/mcp.json",
                "key": "mcpServers",
                "side": "windows",
                "create_ok": True,
            },
            {
                "name": "Windsurf",
                "path": f"{wu}/.windsurf/mcp_config.json",
                "key": "mcpServers",
                "side": "windows",
                "create_ok": True,
            },
            {
                "name": "Windsurf (VS Code ext)",
                "path": f"{wu}/.codeium/windsurf/mcp_config.json",
                "key": "mcpServers",
                "side": "windows",
                "create_ok": True,
            },
            {
                "name": "VS Code",
                "path": f"{wu}/AppData/Roaming/Code/User/mcp.json",
                "key": "servers",
                "side": "windows",
                "create_ok": False,
                "entry_extra": {"type": "stdio"},
            },
            {
                "name": "Codex CLI (Windows)",
                "path": f"{wu}/.codex/config.json",
                "key": "mcpServers",
                "side": "windows",
                "create_ok": False,  # only if .codex/ already exists
            },
        ])

    agents.extend([
        {
            "name": "Claude Code",
            "path": f"{home}/.claude.json",
            "key": "mcpServers",
            "side": "linux",
            "create_ok": True,
        },
        {
            "name": "Codex CLI",
            "path": f"{home}/.codex/config.json",
            "key": "mcpServers",
            "side": "linux",
            "create_ok": False,
        },
        {
            "name": "Continue",
            "path": f"{home}/.continue/config.json",
            "key": "mcpServers",
            "side": "linux",
            "create_ok": False,
        },
    ])

    return agents


def _scan_extra_configs(known_paths: set[str]) -> list[dict]:
    """Look for MCP config files in common locations not covered by known agents."""
    found: list[dict] = []
    home = Path.home()
    win_user = _detect_windows_user()

    # Targeted filenames that are likely MCP configs
    candidates: list[Path] = []

    if win_user:
        wu = Path(f"/mnt/c/Users/{win_user}")
        candidates.extend([
            wu / ".continue" / "config.json",
        ])

    # Check each candidate
    for p in candidates:
        ps = str(p)
        if ps in known_paths or not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            if "mcpServers" in data:
                side = "windows" if "/mnt/c/" in ps else "linux"
                found.append({
                    "name": f"MCP config ({p.name} in {p.parent.name}/)",
                    "path": ps,
                    "key": "mcpServers",
                    "side": side,
                    "create_ok": False,
                })
        except (json.JSONDecodeError, OSError):
            pass

    return found


# ─── Read / Write Config ─────────────────────────────────────────────────────

def _read_config(path: str) -> dict | None:
    """Read a JSON config file, return None if it doesn't exist or is invalid."""
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: Could not parse {path}: {e}", file=sys.stderr)
        return None


def _write_config(path: str, data: dict) -> None:
    """Write a JSON config file, creating parent directories if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def _entry_matches(existing: dict, desired: dict) -> bool:
    """Check if an existing MCP entry already matches what we'd inject."""
    return (
        existing.get("command") == desired.get("command")
        and existing.get("args") == desired.get("args")
    )


# ─── Core Logic ──────────────────────────────────────────────────────────────

def _process_agent(agent: dict, check_only: bool) -> str:
    """
    Process a single agent config. Returns a status string:
    'wired', 'up-to-date', 'updated', 'created', 'skipped', 'not-found', 'error'
    """
    name = agent["name"]
    path = agent["path"]
    key = agent["key"]
    side = agent["side"]
    create_ok = agent.get("create_ok", False)

    base_entry = _windows_entry() if side == "windows" else _linux_entry()
    entry_extra = agent.get("entry_extra", {})
    desired = {**entry_extra, **base_entry}
    config = _read_config(path)

    if config is None:
        # Config file doesn't exist
        if not create_ok:
            return "not-installed"
        parent = Path(path).parent
        if not parent.exists() and not create_ok:
            return "not-installed"
        if check_only:
            return "needs-config"
        # Create new config with the MCP entry
        new_config = {key: {MCP_NAME: desired}}
        try:
            _write_config(path, new_config)
            return "created"
        except OSError as e:
            print(f"  ERROR writing {path}: {e}", file=sys.stderr)
            return "error"

    # Config exists — check for our entry
    servers = config.get(key, {})
    if not isinstance(servers, dict):
        servers = {}

    existing = servers.get(MCP_NAME)

    if existing is not None:
        # Entry exists — check if it matches
        if _entry_matches(existing, desired):
            return "up-to-date"
        if check_only:
            return "needs-update"
        # Update it — merge to preserve extra fields (e.g. env)
        servers[MCP_NAME] = {**existing, **desired}
        config[key] = servers
        try:
            _write_config(path, config)
            return "updated"
        except OSError as e:
            print(f"  ERROR writing {path}: {e}", file=sys.stderr)
            return "error"

    # Entry doesn't exist yet
    if check_only:
        return "needs-config"
    servers[MCP_NAME] = desired
    config[key] = servers
    try:
        _write_config(path, config)
        return "wired"
    except OSError as e:
        print(f"  ERROR writing {path}: {e}", file=sys.stderr)
        return "error"


# ─── Claude Code Hooks ────────────────────────────────────────────────────────

HOOKS_DIR = Path(__file__).parent / "hooks"
HOOK_FILES = ["brain-reminder.sh", "require-brain-search.sh"]

HOOKS_CONFIG = {
    "UserPromptSubmit": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "",  # filled at install time
                    "timeout": 5,
                }
            ]
        }
    ],
    "PreToolUse": [
        {
            "matcher": "(?!mcp__open-brain__).*",
            "hooks": [
                {
                    "type": "command",
                    "command": "",  # filled at install time
                    "timeout": 10,
                }
            ]
        }
    ],
}


def _get_claude_hooks_dest() -> Path:
    """Return the path to ~/.claude/hooks/, cross-platform."""
    win_user = _detect_windows_user()
    if win_user and sys.platform != "darwin":
        # Running from WSL -- target the Windows-side config
        return Path(f"/mnt/c/Users/{win_user}/.claude/hooks")
    return Path.home() / ".claude" / "hooks"


def _get_claude_settings_path() -> Path:
    """Return the path to ~/.claude/settings.json, cross-platform."""
    return _get_claude_hooks_dest().parent / "settings.json"


def _install_claude_hooks(check_only: bool = False) -> str:
    """
    Copy hook scripts to ~/.claude/hooks/ and register them in settings.json.
    Returns status: 'installed', 'up-to-date', 'needs-install', 'error'
    """
    settings_path = _get_claude_settings_path()
    hooks_dest = _get_claude_hooks_dest()

    # Check if source hooks exist in the repo
    for hf in HOOK_FILES:
        if not (HOOKS_DIR / hf).exists():
            print(f"  WARNING: Hook source missing: {HOOKS_DIR / hf}", file=sys.stderr)
            return "error"

    # Check if hooks are already installed
    hooks_present = all((hooks_dest / hf).exists() for hf in HOOK_FILES)
    settings_has_hooks = False

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            settings_has_hooks = (
                "hooks" in settings
                and "UserPromptSubmit" in settings.get("hooks", {})
                and "PreToolUse" in settings.get("hooks", {})
            )
        except (json.JSONDecodeError, OSError):
            pass

    if hooks_present and settings_has_hooks:
        return "up-to-date"

    if check_only:
        return "needs-install"

    # --- Install hooks ---
    import shutil

    # 1. Copy hook scripts
    hooks_dest.mkdir(parents=True, exist_ok=True)
    for hf in HOOK_FILES:
        src = HOOKS_DIR / hf
        dst = hooks_dest / hf
        shutil.copy2(src, dst)
        try:
            dst.chmod(0o755)
        except OSError:
            pass  # Windows doesn't support chmod

    # 2. Build command strings using installed paths
    reminder_path = str(hooks_dest / "brain-reminder.sh")
    blocker_path = str(hooks_dest / "require-brain-search.sh")

    hooks_config = json.loads(json.dumps(HOOKS_CONFIG))  # deep copy
    hooks_config["UserPromptSubmit"][0]["hooks"][0]["command"] = f'bash "{reminder_path}"'
    hooks_config["PreToolUse"][0]["hooks"][0]["command"] = f'bash "{blocker_path}"'

    # 3. Merge into settings.json (preserve ALL existing settings)
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = {}

    settings["hooks"] = hooks_config

    try:
        _write_config(str(settings_path), settings)
        return "installed"
    except OSError as e:
        print(f"  ERROR writing {settings_path}: {e}", file=sys.stderr)
        return "error"


# ─── Public Interface ─────────────────────────────────────────────────────────

STATUS_ICONS = {
    "up-to-date":    "  OK ",
    "wired":         " NEW ",
    "created":       " NEW ",
    "updated":       " UPD ",
    "needs-config":  "MISS ",
    "needs-update":  " OLD ",
    "not-installed": "  -  ",
    "error":         " ERR ",
}


def run_wire(check_only: bool = False) -> list[dict]:
    """
    Scan for AI agents and wire them to Open Brain.

    Returns a list of {name, path, status} dicts.
    """
    mode = "CHECK" if check_only else "WIRE"
    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  Open Brain — Agent {mode}", file=sys.stderr)
    print(f"{'=' * 60}\n", file=sys.stderr)

    agents = _get_agents()
    known_paths = {a["path"] for a in agents}
    extras = _scan_extra_configs(known_paths)
    all_agents = agents + extras

    results: list[dict] = []

    for agent in all_agents:
        status = _process_agent(agent, check_only)
        icon = STATUS_ICONS.get(status, "  ?  ")
        results.append({"name": agent["name"], "path": agent["path"], "status": status})
        print(f"  [{icon}]  {agent['name']}", file=sys.stderr)
        if status != "not-installed":
            print(f"          {agent['path']}", file=sys.stderr)

    # Claude Code hooks (enforcement)
    print(f"\n  {'─' * 56}", file=sys.stderr)
    print(f"  Claude Code Hooks (brain-search enforcement)", file=sys.stderr)
    print(f"  {'─' * 56}", file=sys.stderr)
    hook_status = _install_claude_hooks(check_only)
    hook_icon = {
        "up-to-date": "  OK ", "installed": " NEW ",
        "needs-install": "MISS ", "error": " ERR ",
    }.get(hook_status, "  ?  ")
    results.append({"name": "Claude Code Hooks", "path": str(_get_claude_settings_path()), "status": hook_status})
    print(f"  [{hook_icon}]  Claude Code Hooks", file=sys.stderr)
    print(f"          {_get_claude_hooks_dest()}", file=sys.stderr)
    if hook_status == "installed":
        print(f"          Hooks installed. Restart Claude Code to activate.", file=sys.stderr)

    # Summary
    wired = [r for r in results if r["status"] in ("wired", "created", "updated", "installed")]
    ok = [r for r in results if r["status"] == "up-to-date"]
    missing = [r for r in results if r["status"] in ("needs-config", "needs-update", "needs-install")]
    errors = [r for r in results if r["status"] == "error"]
    skipped = [r for r in results if r["status"] == "not-installed"]

    print(f"\n{'─' * 60}", file=sys.stderr)
    if check_only:
        print(f"  Already wired: {len(ok)}", file=sys.stderr)
        if missing:
            print(f"  Need wiring:   {len(missing)}", file=sys.stderr)
            for r in missing:
                print(f"    -> {r['name']}", file=sys.stderr)
        if not missing:
            print("  All detected agents are wired!", file=sys.stderr)
    else:
        if wired:
            print(f"  Configured:    {len(wired)}", file=sys.stderr)
            for r in wired:
                print(f"    -> {r['name']} ({r['status']})", file=sys.stderr)
        if ok:
            print(f"  Already OK:    {len(ok)}", file=sys.stderr)
        if errors:
            print(f"  Errors:        {len(errors)}", file=sys.stderr)

    if skipped:
        print(f"  Not installed: {len(skipped)}", file=sys.stderr)

    entry_info = _linux_entry()
    print(f"\n  MCP entry (Linux/WSL):", file=sys.stderr)
    print(f"    command: {entry_info['command']}", file=sys.stderr)
    print(f"    args:    {entry_info['args']}", file=sys.stderr)

    print(f"{'=' * 60}\n", file=sys.stderr)
    return results


def run_check_quiet() -> list[dict]:
    """
    Run a quiet check — returns results without the full banner.
    Used for first-run and periodic checks.
    """
    agents = _get_agents()
    known_paths = {a["path"] for a in agents}
    extras = _scan_extra_configs(known_paths)

    results: list[dict] = []
    for agent in agents + extras:
        status = _process_agent(agent, check_only=True)
        if status != "not-installed":
            results.append({"name": agent["name"], "status": status})
    return results


def print_first_run_notice(results: list[dict]) -> None:
    """Print a notice about unwired agents if any are found."""
    missing = [r for r in results if r["status"] in ("needs-config", "needs-update")]
    if not missing:
        return
    print("\n  ⚠  Agents detected but not wired to Open Brain:", file=sys.stderr)
    for r in missing:
        print(f"     → {r['name']}", file=sys.stderr)
    print("     Run: python server.py wire", file=sys.stderr)
    print("", file=sys.stderr)
