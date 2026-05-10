#!/usr/bin/env python3
"""
session-end-save.py — Stop hook for Open Brain.

Parses the conversation transcript at session end and:
1. Writes a structured handoff file to the project directory so the next
   session can pick up where this one left off.
2. Writes a summary directly to both Open Brain databases (V1 + V2) if
   they are reachable.

Install: copy to ~/.claude/hooks/ and register in settings.json under
the "Stop" hook event. See settings.snippet.json.

Customize PROJECT_MAP to match your project directory names.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_USER_MESSAGES = 20
HANDOFF_MAX_CHARS = 4000

# Read DB URLs from environment — set these in your shell profile or .env
V1_DB = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/openbrain")
V2_DB = os.getenv("OPEN_BRAIN_V2_DATABASE_URL", "postgresql://postgres:password@localhost:5433/open_brain_v2")

# ── Customize: map directory name fragments to project slugs ─────────────────
# Add entries for each project you work on. The key is a case-insensitive
# substring match against the working directory path.
PROJECT_MAP = [
    ("open-brain", "open-brain"),
    # ("my-app",     "my-app"),
    # ("backend",    "my-backend"),
]


def detect_project(cwd: str) -> str:
    low = cwd.lower().replace("\\", "/")
    for pat, name in PROJECT_MAP:
        if pat in low:
            return name
    return Path(cwd).name.lower()


def project_key(cwd: str) -> str:
    norm = cwd.replace("\\", "/").rstrip("/")
    if len(norm) >= 2 and norm[1] == ":":
        return norm[0].upper() + "--" + norm[2:].lstrip("/").replace("/", "-")
    return norm.replace("/", "-").lstrip("-")


def parse_transcript(path: str) -> dict:
    user_msgs, tool_calls, files_edited = [], [], set()
    git_ops, brain_calls, branch = [], [], None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("message", {})
                role = msg.get("role", "")
                content = msg.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]

                if role == "user":
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text and not text.startswith("<"):
                                user_msgs.append(text[:300])

                if role == "assistant":
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            name = block.get("name", "")
                            inp = block.get("input", {})
                            tool_calls.append(name)
                            if name in ("Edit", "Write"):
                                fp = inp.get("file_path", "")
                                if fp:
                                    files_edited.add(Path(fp).name)
                            if name == "Bash":
                                cmd = inp.get("command", "")
                                if "git" in cmd:
                                    git_ops.append(cmd[:120])
                                    m = re.search(r"git checkout -b ([^\s]+)", cmd)
                                    if m:
                                        branch = m.group(1)
                            if "open-brain" in name:
                                brain_calls.append(name)
    except Exception:
        pass

    return {
        "user_msgs": user_msgs[-MAX_USER_MESSAGES:],
        "tool_calls": list(set(tool_calls)),
        "files_edited": sorted(files_edited),
        "git_ops": git_ops[-10:],
        "brain_calls": list(set(brain_calls)),
        "branch": branch,
    }


def build_handoff(data: dict, project: str, cwd: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"HANDOFF {now} | project={project} | cwd={cwd}"]
    if data["branch"]:
        lines.append(f"Branch: {data['branch']}")
    if data["files_edited"]:
        lines.append(f"Files edited: {', '.join(data['files_edited'])}")
    if data["git_ops"]:
        lines.append("Git ops: " + "; ".join(data["git_ops"][-5:]))
    if data["brain_calls"]:
        lines.append(f"Brain calls: {', '.join(sorted(set(data['brain_calls'])))}")
    if data["user_msgs"]:
        lines.append("\nUser requests this session:")
        for m in data["user_msgs"][-5:]:
            lines.append(f"  - {m[:200]}")
    return "\n".join(lines)[:HANDOFF_MAX_CHARS]


def write_to_v1(handoff: str, project: str) -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(V1_DB, connect_timeout=3)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memories (content, type, project, source) VALUES (%s, %s, %s, %s)",
                (handoff, "note", project, "session-end-hook"),
            )
        conn.close()
        return True
    except Exception:
        return False


def write_to_v2(handoff: str, project: str) -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(V2_DB, connect_timeout=3)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO facts (content, project, source, created_at) VALUES (%s, %s, %s, NOW())",
                (handoff, project, "session-end-hook"),
            )
        conn.close()
        return True
    except Exception:
        return False


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    transcript = payload.get("transcript_path", "")
    cwd = payload.get("cwd", os.getcwd())
    project = detect_project(cwd)

    if not transcript or not os.path.isfile(transcript):
        return

    data = parse_transcript(transcript)
    if not data["user_msgs"] and not data["files_edited"]:
        return  # Nothing meaningful happened

    handoff = build_handoff(data, project, cwd)

    # Write handoff file to project root
    try:
        handoff_path = Path(cwd) / "LAST_SESSION_HANDOFF.md"
        handoff_path.write_text(handoff, encoding="utf-8")
    except Exception:
        pass

    # Write to both brain DBs (best-effort, never fail)
    write_to_v1(handoff, project)
    write_to_v2(handoff, project)


if __name__ == "__main__":
    main()
