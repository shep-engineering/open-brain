"""brain_kg — an ADJACENT knowledge-graph memory engine.

Built alongside brain_v2 (2026-09-11) to test the thesis that a temporal
knowledge graph over the SAME memory corpus retrieves connected, multi-hop
memories where brain_v2's flat vector-cosine retrieval returns adjacent noise.

NON-DESTRUCTIVE BY CONSTRUCTION:
  - This package NEVER writes to the brain's database (`open_brain_v2`).
  - It reads the brain corpus read-only (ingest.py, in a single autocommit-off,
    read-only transaction) and projects it into its OWN database (`open_brain_kg`).
  - The KG DB is a derived, disposable projection. Dropping it loses nothing that
    is not reconstructable from the brain.

It reuses brain_v2's embedding config and, where possible, brain_v2's ALREADY
COMPUTED embeddings (copied, not recomputed) so the vector-seed step is identical
to the brain's — the only measured difference between the two engines is the
graph expansion.
"""
