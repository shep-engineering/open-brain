"""brain_kg configuration.

Reuses brain_v2's embedding + Postgres server, but points at a SEPARATE
database (`open_brain_kg`) so nothing here can touch the live brain.
"""
from __future__ import annotations

import os

# Reuse brain_v2's embedding constants verbatim so the vector-seed step is
# identical between the two engines (fair comparison).
from brain_v2.config import (  # noqa: F401
    EMBEDDING_DIMS,
    OLLAMA_EMBED_BASE_URL,
    OLLAMA_EMBED_MODEL,
    OLLAMA_EMBED_TIMEOUT,
)
from brain_v2.config import DATABASE_URL as BRAIN_DATABASE_URL

# The KG's OWN database. Same Postgres server as the brain (port 5433), but a
# distinct database — a bug here cannot corrupt open_brain_v2.
KG_DATABASE_URL = os.getenv(
    "OPEN_BRAIN_KG_DATABASE_URL",
    "postgresql://postgres:password@localhost:5433/open_brain_kg",
)

# The brain database, READ-ONLY source for ingest. Defaults to the brain's own
# configured URL so ingest always reads the same DB the brain writes.
KG_SOURCE_BRAIN_URL = os.getenv("OPEN_BRAIN_KG_SOURCE_URL", BRAIN_DATABASE_URL)

# Graph retrieval defaults.
KG_DEFAULT_HOPS = int(os.getenv("OPEN_BRAIN_KG_HOPS", "1"))
KG_SEED_K = int(os.getenv("OPEN_BRAIN_KG_SEED_K", "8"))
KG_RESULT_K = int(os.getenv("OPEN_BRAIN_KG_RESULT_K", "10"))
# Edge-weight decay per hop from the seed (a 2-hop node counts less than a 1-hop).
KG_HOP_DECAY = float(os.getenv("OPEN_BRAIN_KG_HOP_DECAY", "0.6"))

# Entity-hop scoring (DIFF-gate flagged these as unswept; now config-driven so the
# sweep harness can vary them). score = seed_base * (FLOOR + (1-FLOOR)*(1 - DECAY^support)).
KG_ENT_SUPPORT_DECAY = float(os.getenv("OPEN_BRAIN_KG_ENT_SUPPORT_DECAY", "0.6"))
KG_ENT_FLOOR = float(os.getenv("OPEN_BRAIN_KG_ENT_FLOOR", "0.5"))
KG_ENT_COMENTION_W = float(os.getenv("OPEN_BRAIN_KG_ENT_COMENTION_W", "1.0"))
KG_ENT_RELATION_W = float(os.getenv("OPEN_BRAIN_KG_ENT_RELATION_W", "0.5"))

# Edge-construction params.
KG_NEIGHBOR_K = int(os.getenv("OPEN_BRAIN_KG_NEIGHBOR_K", "5"))       # top-N cosine neighbors per node
KG_NEIGHBOR_MIN_SIM = float(os.getenv("OPEN_BRAIN_KG_NEIGHBOR_MIN_SIM", "0.55"))
KG_SAME_PROJECT_CAP = int(os.getenv("OPEN_BRAIN_KG_SAME_PROJECT_CAP", "3"))  # cap same_project edges/node

# SAFETY (PLAN-gate #1): ingest reads the brain read-only, single connection.
# Belt-and-suspenders: options string forces read-only at the session level even
# on the superuser connection, so a coding slip cannot write to the brain.
KG_SOURCE_READONLY_OPTIONS = os.getenv(
    "OPEN_BRAIN_KG_SOURCE_OPTIONS",
    "-c default_transaction_read_only=on",
)
