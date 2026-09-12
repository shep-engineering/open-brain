"""brain_kg edge construction — deterministic, from signals the corpus HAS.

Three edge classes only (PLAN-gate #2 re-scope):
  - supersedes:   from rules.superseded_by (130 real edges). corrector -> retired.
                  The retired node is marked active=FALSE.
  - neighbor:     top-N cosine neighbors per node over the COPIED embeddings.
                  This is what makes the graph traversable; it adds STRUCTURE
                  over the brain's own embeddings, no new signal.
  - same_project: light grouping edges among nodes sharing a project, capped.

Reads supersede pairs from the brain READ-ONLY; everything else runs inside the
KG DB against the copied embeddings.
"""
from __future__ import annotations

from typing import Any

from . import store
from .config import KG_NEIGHBOR_K, KG_NEIGHBOR_MIN_SIM, KG_SAME_PROJECT_CAP


def _node_id_for(cur, kind: str, memory_id: int) -> int | None:
    cur.execute("SELECT id FROM kg_nodes WHERE kind = %s AND memory_id = %s", (kind, memory_id))
    row = cur.fetchone()
    return row[0] if row else None


def build_supersede_edges(kg, brain_ro) -> int:
    """corrector rule --supersedes--> retired rule. Mark retired inactive."""
    with brain_ro.cursor() as cur:
        cur.execute(
            "SELECT id, superseded_by FROM rules WHERE superseded_by IS NOT NULL"
        )
        pairs = cur.fetchall()  # (retired_id, corrector_id)
    built = 0
    with kg.cursor() as cur:
        for retired_id, corrector_id in pairs:
            retired = _node_id_for(cur, "rule", retired_id)
            corrector = _node_id_for(cur, "rule", corrector_id)
            if retired is None or corrector is None:
                continue
            store.add_edge(kg, src_id=corrector, dst_id=retired,
                           relation="supersedes", weight=1.0,
                           provenance_memory_id=corrector_id)
            # Belief-time: the retired rule is no longer the current belief.
            cur.execute("UPDATE kg_nodes SET active = FALSE WHERE id = %s", (retired,))
            built += 1
    kg.commit()
    return built


def build_neighbor_edges(kg) -> int:
    """For each ACTIVE node, connect its top-K cosine neighbors (over copied
    embeddings). Undirected in effect (we add corrector->neighbor); the walk
    treats neighbor edges as traversable from src."""
    built = 0
    with kg.cursor() as cur:
        cur.execute("SELECT id FROM kg_nodes WHERE embedding IS NOT NULL AND active = TRUE")
        node_ids = [r[0] for r in cur.fetchall()]
    with kg.cursor() as cur:
        for nid in node_ids:
            cur.execute(
                """
                SELECT n2.id, 1 - (n1.embedding <=> n2.embedding) AS sim
                FROM kg_nodes n1, kg_nodes n2
                WHERE n1.id = %s AND n2.id <> n1.id
                  AND n2.embedding IS NOT NULL AND n2.active = TRUE
                ORDER BY n1.embedding <=> n2.embedding
                LIMIT %s
                """,
                (nid, KG_NEIGHBOR_K),
            )
            for dst, sim in cur.fetchall():
                if sim is None or sim < KG_NEIGHBOR_MIN_SIM:
                    continue
                store.add_edge(kg, src_id=nid, dst_id=dst, relation="neighbor",
                               weight=float(sim), provenance_memory_id=None)
                built += 1
    kg.commit()
    return built


def build_same_project_edges(kg) -> int:
    """Light grouping edges among nodes sharing a non-empty project, capped per
    node so dense projects don't create a hairball. Chooses the cap nearest
    neighbors within the project (by recency proxy = id) to keep it cheap."""
    built = 0
    with kg.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT project FROM kg_nodes WHERE project <> '' AND active = TRUE"
        )
        projects = [r[0] for r in cur.fetchall()]
    with kg.cursor() as cur:
        for proj in projects:
            cur.execute(
                "SELECT id FROM kg_nodes WHERE project = %s AND active = TRUE ORDER BY id DESC",
                (proj,),
            )
            ids = [r[0] for r in cur.fetchall()]
            # link each node to the next `cap` nodes in the list (bounded degree)
            for i, nid in enumerate(ids):
                for dst in ids[i + 1: i + 1 + KG_SAME_PROJECT_CAP]:
                    store.add_edge(kg, src_id=nid, dst_id=dst, relation="same_project",
                                   weight=0.3, provenance_memory_id=None)
                    built += 1
    kg.commit()
    return built


import re

_CORRECTS_RE = re.compile(r"\bcorrects?\b[^.]{0,40}?\bfact\s+#?(\d+)", re.IGNORECASE)


def build_fact_supersede_edges(kg) -> int:
    """The correction primitive FACTS lack in the brain.

    Two signals, both content/structure based (NOT chosen to fit the gold):
      1. EXPLICIT: a fact body that says 'corrects fact N' supersedes fact N.
         (Deterministic parse of the body text.)
      2. IMPLICIT: two facts on the same topic (cosine > threshold) where the
         NEWER (higher id) supersedes the OLDER. Point-in-time state snapshots
         are the target: the latest snapshot is the current belief.
    The superseded (older/wrong) fact node is marked active=FALSE in the KG.
    """
    built = 0
    # 1. explicit "corrects fact N"
    with kg.cursor() as cur:
        cur.execute("SELECT id, memory_id, body FROM kg_nodes WHERE kind='fact'")
        fact_rows = cur.fetchall()
        by_mem = {}
        cur.execute("SELECT memory_id, id FROM kg_nodes WHERE kind='fact'")
        for mem, nid in cur.fetchall():
            by_mem[mem] = nid
        for nid, mem, body in fact_rows:
            for m in _CORRECTS_RE.finditer(body or ""):
                older_mem = int(m.group(1))
                older_nid = by_mem.get(older_mem)
                if older_nid and older_nid != nid:
                    store.add_edge(kg, src_id=nid, dst_id=older_nid,
                                   relation="supersedes", weight=1.0,
                                   provenance_memory_id=mem)
                    cur.execute("UPDATE kg_nodes SET active=FALSE WHERE id=%s", (older_nid,))
                    built += 1
    kg.commit()
    # 2. NARROW temporal-state supersession (DIFF-gate #3 fix).
    # The blind cosine>0.82 heuristic was REMOVED: it false-demoted ~17/19
    # distinct co-valid facts (bio/resume items 16,17,27-32). Point-in-time
    # STATE snapshots are the only safe implicit target, gated by ALL of:
    #   (a) both bodies carry a state-snapshot marker (STATE/REMAINING/OVERNIGHT/
    #       NEXT/DEPLOY STATE) — i.e. they ARE dated status snapshots, not facts;
    #   (b) same non-empty project;
    #   (c) high mutual cosine (same topic);
    #   (d) newer (higher id) supersedes older.
    # This targets stale deploy-state walls without touching distinct facts.
    marker = r"(STATE FOR|OVERNIGHT STATE|REMAINING to|DEPLOY STATE|NEXT:|CURRENT STATE|LIVE STATE)"
    sim_floor = float(__import__("os").getenv("OPEN_BRAIN_KG_FACT_SUPERSEDE_SIM", "0.80"))
    with kg.cursor() as cur:
        cur.execute(
            """
            SELECT n1.id AS newer, n2.id AS older
            FROM kg_nodes n1 JOIN kg_nodes n2
              ON n1.kind='fact' AND n2.kind='fact' AND n1.memory_id > n2.memory_id
            WHERE n1.embedding IS NOT NULL AND n2.embedding IS NOT NULL
              AND n1.active AND n2.active
              AND n1.project <> '' AND n1.project = n2.project
              AND n1.body ~ %s AND n2.body ~ %s
              AND (1-(n1.embedding <=> n2.embedding)) > %s
            """,
            (marker, marker, sim_floor),
        )
        for newer, older in cur.fetchall():
            store.add_edge(kg, src_id=newer, dst_id=older, relation="supersedes",
                           weight=0.85, provenance_memory_id=None)
            cur.execute("UPDATE kg_nodes SET active=FALSE WHERE id=%s", (older,))
            built += 1
    kg.commit()
    # 3. ENTITY-OVERLAP state supersession (DIFF-gate #3: the signal cosine MISSES).
    # Successive state snapshots of one evolving thing have LOW mutual cosine
    # (measured 0.47-0.71) but SHARE distinctive entity tokens (ticket ids,
    # migration numbers, named subsystems). Deterministic extraction:
    #   - both facts carry a state-snapshot marker;
    #   - same non-empty project;
    #   - share >= MIN_SHARED distinctive tokens;
    #   - newer (higher id) supersedes older.
    # This is real (cheap, deterministic) entity extraction — the Tier-A signal
    # that actually works for evolving state, unlike blind cosine.
    built += _build_entity_overlap_edges(kg)
    kg.commit()
    return built


_TOKEN_RE = re.compile(r"(ARC-\d+|PR ?#?\d+|\b0\d{2}\b|demo-green|dba[- ]?credential"
                       r"|migrator|owner-grant|data_classification|RLS|SSM|erasure)",
                       re.IGNORECASE)
_STATE_MARKER_RE = re.compile(
    r"(STATE FOR|OVERNIGHT STATE|REMAINING to|DEPLOY STATE|NEXT:|CURRENT STATE"
    r"|LIVE STATE|CREDENTIAL CHAIN|findings|COMPLETE|VERIFIED LIVE)", re.IGNORECASE)


def _distinctive_tokens(body: str) -> set[str]:
    return {m.group(0).lower().replace(" ", "").replace("#", "") for m in _TOKEN_RE.finditer(body or "")}


def _build_entity_overlap_edges(kg, min_shared: int = 2) -> int:
    """Same-project, state-marked facts sharing >= min_shared distinctive tokens:
    newer supersedes older. Reads bodies from the KG nodes (already copied)."""
    built = 0
    with kg.cursor() as cur:
        cur.execute(
            "SELECT id, memory_id, project, body FROM kg_nodes "
            "WHERE kind='fact' AND active AND project <> ''"
        )
        facts = [(nid, mem, proj, body) for nid, mem, proj, body in cur.fetchall()
                 if _STATE_MARKER_RE.search(body or "")]
    # group by project, compare pairs (small N after the marker filter)
    by_proj: dict[str, list] = {}
    for nid, mem, proj, body in facts:
        by_proj.setdefault(proj, []).append((nid, mem, _distinctive_tokens(body)))
    with kg.cursor() as cur:
        for proj, items in by_proj.items():
            items.sort(key=lambda t: t[1])  # by memory_id asc
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    older_nid, older_mem, older_tok = items[i]
                    newer_nid, newer_mem, newer_tok = items[j]
                    shared = older_tok & newer_tok
                    if len(shared) >= min_shared:
                        store.add_edge(kg, src_id=newer_nid, dst_id=older_nid,
                                       relation="supersedes", weight=0.8,
                                       provenance_memory_id=None)
                        cur.execute("UPDATE kg_nodes SET active=FALSE WHERE id=%s",
                                    (older_nid,))
                        built += 1
    kg.commit()
    return built


def build_all() -> dict[str, Any]:
    kg = store.kg_conn()
    brain = store.brain_ro_conn()
    try:
        sup = build_supersede_edges(kg, brain)
    finally:
        brain.close()
    fact_sup = build_fact_supersede_edges(kg)
    nbr = build_neighbor_edges(kg)
    proj = build_same_project_edges(kg)
    report = {"supersede_edges": sup, "fact_supersede_edges": fact_sup,
              "neighbor_edges": nbr, "same_project_edges": proj,
              **store.counts(kg)}
    kg.close()
    return report


if __name__ == "__main__":  # pragma: no cover
    import pprint
    pprint.pprint(build_all())
