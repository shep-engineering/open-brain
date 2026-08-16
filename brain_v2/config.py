"""Open Brain v2 configuration.

All values are environment-overridable. Defaults target the v2 container
on port 5433 (separate from v1's 5432).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

DATABASE_URL = os.getenv(
    "OPEN_BRAIN_V2_DATABASE_URL",
    "postgresql://postgres:password@localhost:5433/open_brain_v2",
)

EMBEDDING_DIMS = int(os.getenv("OPEN_BRAIN_V2_EMBEDDING_DIMS", "4096"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
# Embeddings hit a dedicated ollama instance pinned to the RTX 3080 Ti; defaults
# to the main URL so single-instance setups keep working.
OLLAMA_EMBED_BASE_URL = os.getenv("OLLAMA_EMBED_BASE_URL", OLLAMA_BASE_URL)
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:8b")
OLLAMA_EMBED_TIMEOUT = int(os.getenv("OPEN_BRAIN_V2_EMBED_TIMEOUT", "120"))

BOOT_TOKEN_CAP = int(os.getenv("OPEN_BRAIN_V2_BOOT_TOKEN_CAP", "2000"))
BOOT_BLOCKER_COUNT_CAP = int(os.getenv("OPEN_BRAIN_V2_BOOT_BLOCKER_CAP", "5"))
BOOT_PATTERN_COUNT_CAP = int(os.getenv("OPEN_BRAIN_V2_BOOT_PATTERN_CAP", "5"))
BOOT_HANDOFF_TOKEN_CAP = int(os.getenv("OPEN_BRAIN_V2_BOOT_HANDOFF_CAP", "200"))
BOOT_TASK_COUNT_CAP = int(os.getenv("OPEN_BRAIN_V2_BOOT_TASK_CAP", "20"))

HEADLINE_WORD_CAP = int(os.getenv("OPEN_BRAIN_V2_HEADLINE_WORD_CAP", "15"))
BODY_WORD_CAP = int(os.getenv("OPEN_BRAIN_V2_BODY_WORD_CAP", "200"))

DUPLICATE_COSINE_THRESHOLD = float(
    os.getenv("OPEN_BRAIN_V2_DUPLICATE_COSINE", "0.75")
)

# Lower bound for the "similar existing rule" write-time hint. A new rule whose
# nearest active same-project neighbor is in [SIMILAR_RULE_COSINE, DUPLICATE_COSINE)
# is not a duplicate, but IS about the same topic — surfaced as a non-blocking
# hint so the agent can consider superseding instead of adding a parallel rule.
# UNCALIBRATED default — tune on real corpus data (dump pairwise cosine, label a
# sample, pick from a precision curve). Set below the dedup threshold.
SIMILAR_RULE_COSINE = float(
    os.getenv("OPEN_BRAIN_V2_SIMILAR_RULE_COSINE", "0.62")
)

# Threshold for the on-demand consolidation-candidate finder: pairs of active
# rules with cosine >= this are candidates to review for merging (supersede into
# one). IMPORTANT (measured): it must sit AT/BELOW the dedup threshold (0.75), NOT
# above it. Anything >= 0.75 was already blocked at WRITE time by find_duplicate, so
# a value like 0.80 would find almost nothing among rules written through the gate.
# The pile-up this tool targets lives just under the dedup line, and in rules dedup
# never saw (written pre-dedup, or cross-project near-dups the same-project write
# hint doesn't cover). Default 0.72 catches the survivable band. UNCALIBRATED —
# tune on real data. Cosine is an imperfect proxy, so the tool surfaces
# bodies/severity/pinned for the agent to judge.
CONSOLIDATION_COSINE = float(
    os.getenv("OPEN_BRAIN_V2_CONSOLIDATION_COSINE", "0.72")
)
# Refuse the O(N^2) consolidation scan above this many active rules (guards the
# 10s slow-call threshold; the pairwise self-join has no vector index).
CONSOLIDATION_MAX_RULES = int(
    os.getenv("OPEN_BRAIN_V2_CONSOLIDATION_MAX_RULES", "500")
)

FACT_DECAY_HALFLIFE_DAYS = float(
    os.getenv("OPEN_BRAIN_V2_FACT_HALFLIFE_DAYS", "7.0")
)
# Ebbinghaus decay score threshold below which a FACT is deactivated from
# memory_index. Score = 2^(-Δdays / halflife). At halflife=7 and
# threshold=0.1, deactivation happens ~23 days after last access.
FACT_DECAY_SCORE_THRESHOLD = float(
    os.getenv("OPEN_BRAIN_V2_FACT_DECAY_THRESHOLD", "0.1")
)
INCIDENT_ARCHIVE_DAYS = int(os.getenv("OPEN_BRAIN_V2_INCIDENT_ARCHIVE_DAYS", "90"))

# Prune safeguards (mirrors v1 PRUNE_MIN_DAYS / PRUNE_MAX_DELETE exactly).
# Hard floor: never prune anything newer than this. Hard cap: never delete
# more than this many rows per call. These prevent the class of failure
# documented in guardrail #827 (2026-03-31 prune wipe of 730 memories).
PRUNE_MIN_DAYS = int(os.getenv("OPEN_BRAIN_V2_PRUNE_MIN_DAYS", "30"))
PRUNE_MAX_DELETE = int(os.getenv("OPEN_BRAIN_V2_PRUNE_MAX_DELETE", "50"))

# Skills layer
SKILL_TRIGGER_MAX = int(os.getenv("OPEN_BRAIN_V2_SKILL_TRIGGER_MAX", "5"))

# Observability
SLOW_CALL_THRESHOLD_MS = int(os.getenv("OPEN_BRAIN_V2_SLOW_CALL_MS", "10000"))

SERVER_NAME = "open-brain-v2"
