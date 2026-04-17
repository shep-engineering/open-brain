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

EMBEDDING_DIMS = int(os.getenv("OPEN_BRAIN_V2_EMBEDDING_DIMS", "768"))
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
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
