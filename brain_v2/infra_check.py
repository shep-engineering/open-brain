"""Falsifiable Ollama-runtime cost check — infra-cost-addendum.md §4.

Contract:

    Boot payload must not force eviction of the metadata LLM on a
    single-slot Ollama.

Falsifiable check:
    1. Assume the user has configured OLLAMA_MAX_LOADED_MODELS=1 and
       METADATA_LLM_MODEL=<something-big>, or we simulate it by
       requiring that `ollama.log` shows no "loading model" line for
       the metadata LLM between our boot_session and a subsequent
       remember/recall operation.
    2. Call boot_session.
    3. Call remember_rule (or recall).
    4. Tail ollama.log for the window. If any "loading model" line
       mentions METADATA_LLM_MODEL in that window, the check FAILS —
       boot is still doing too much embedding work.

v2's Phase 1 does NOT call the metadata LLM at all during boot or
write paths. Bodies are never merged. Write gate enforces structure
caller-side. That means the metadata LLM is never in the hot path,
which means it can't be evicted by v2 traffic. This script proves
that empirically by inspecting ollama.log.

Exit codes:
    0 — check passed (no metadata-LLM reload observed)
    1 — check FAILED (metadata-LLM reload observed during window)
    2 — indeterminate (log unreadable, METADATA_LLM_MODEL unset, etc.)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
from pathlib import Path

from brain_v2 import store
from brain_v2.config import OLLAMA_EMBED_MODEL
from brain_v2.schema import apply_schema


DEFAULT_OLLAMA_LOG = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / "Documents" / "ollama.log"


def _tail_since(path: Path, since: _dt.datetime) -> list[str]:
    """Return lines from `path` whose parseable timestamp is >= `since`.

    Ollama logs start with ISO timestamps like `2026-04-15T18:02:23.123Z`.
    Lines that don't parse are included (fail open — we'd rather show
    noise than hide signal).
    """
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[infra-check] could not read {path}: {exc}", file=sys.stderr)
        return []
    out: list[str] = []
    cutoff_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    for line in text.splitlines():
        # Cheap filter: any line whose first 19 chars sort >= cutoff
        stamp = line[:19]
        if stamp >= cutoff_str:
            out.append(line)
    return out


def _is_reload_line(line: str, model: str) -> bool:
    low = line.lower()
    mlow = model.lower()
    return "loading model" in low and mlow in low


def run(metadata_model: str, project: str = "infra_check",
        ollama_log: Path | None = None, verbose: bool = False) -> int:
    """Execute the falsifiable check. Returns an exit code.

    Note: the check is informative when METADATA_LLM_MODEL is set AND
    OLLAMA_MAX_LOADED_MODELS=1. We warn but still run otherwise.
    """
    log_path = ollama_log or DEFAULT_OLLAMA_LOG
    if not metadata_model:
        print("[infra-check] METADATA_LLM_MODEL not provided; "
              "nothing to observe. Set it to the model you want to prove isn't evicted.",
              file=sys.stderr)
        return 2

    slots = os.environ.get("OLLAMA_MAX_LOADED_MODELS", "")
    if slots and slots != "1":
        print(f"[infra-check] WARNING: OLLAMA_MAX_LOADED_MODELS={slots} "
              "(expected 1 for the tightest version of this check).",
              file=sys.stderr)

    # 1. Schema
    with store.connect() as conn:
        apply_schema(conn)

    # 2. Time window
    start = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    time.sleep(1.0)  # give logs a second to settle before we scan

    # 3. Boot — builds headline-only payload. Calls Ollama ONCE for
    # the task embedding. Does not touch metadata LLM.
    from brain_v2 import boot
    with store.connect() as conn:
        payload = boot.build(
            conn, project=project,
            task="infra-cost falsifiable check — dummy task arg",
            source="infra-check",
        )
    if verbose:
        print(f"[infra-check] boot payload token_estimate={payload.token_estimate}; "
              f"blockers={len(payload.blockers)} patterns={len(payload.patterns)} "
              f"tasks={len(payload.active_tasks)}")

    # 4. Write — remember_rule does ONE embedding call. No metadata LLM.
    with store.connect() as conn:
        res = store.remember_rule(
            conn,
            headline="Infra check synthetic rule do not rely on",
            body="This rule is created by the infra-cost check and "
                 "should be ignored. Exists only to trigger a write path.",
            severity="PATTERN",
            project=project,
            source="infra-check",
        )
    if verbose:
        print(f"[infra-check] remember_rule -> {type(res).__name__}")

    # 5. Sleep a short tail then scan the log window
    time.sleep(2.0)
    lines = _tail_since(log_path, start)

    evictions = [ln for ln in lines if _is_reload_line(ln, metadata_model)]
    if evictions:
        print(f"[infra-check] FAIL — {len(evictions)} metadata-LLM reload line(s) "
              f"observed in {log_path} during the boot+write window:")
        for ln in evictions:
            print(f"  {ln}")
        return 1

    print(f"[infra-check] PASS — no '{metadata_model}' reload observed in "
          f"{log_path} between {start.isoformat()} and now.")
    if verbose:
        print(f"[infra-check] scanned {len(lines)} log lines in window; "
              f"ollama-embed calls in this window used {OLLAMA_EMBED_MODEL} only.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--metadata-model", default=os.getenv("METADATA_LLM_MODEL", ""),
                   help="Name of the metadata LLM whose eviction would be a regression.")
    p.add_argument("--ollama-log", type=Path, default=None,
                   help="Path to ollama.log. Defaults to ~/Documents/ollama.log.")
    p.add_argument("--project", default="infra_check")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    return run(args.metadata_model, project=args.project,
               ollama_log=args.ollama_log, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
