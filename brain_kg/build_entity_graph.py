"""brain_kg KG v3 — build the entity graph from extracted relations.

Reads each kg_nodes.body (the KG COPY of the corpus — NEVER the live brain, NEVER
at query time), runs GLiNER2 extract(), resolves spans to canonical entities
(deterministic ER only), and writes kg_entities / kg_entity_mentions /
kg_entity_edges. Runs alongside the existing cosine graph (fallback lane), which
is left untouched.

Deterministic ER (Phase 2 minimal): canonical_key = lowercase, strip surrounding
articles, collapse whitespace, drop punctuation. Enough to collapse
"088"/"the 088 migration"/"migration 088"/"088 migration" toward one key WITHOUT
fuzzy/embedding/human-review (deferred to 2b, gated on this slice showing signal).
"""
from __future__ import annotations

import re
from typing import Any

from . import store
from .extract import extract, Triple

# Deterministic normalizer. NOT fuzzy — just surface canonicalization.
_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_NONWORD = re.compile(r"[^a-z0-9]+")
# Common noise words that fragment entities ("088 migration" vs "088").
_ENTITY_STOPWORDS = {"migration", "ticket", "issue", "the", "a", "an", "gap"}


# Deterministic entity extractor (KG v3 coverage fix): GLiNER2 missed ~42% of
# gold facts because generic labels don't catch this corpus's jargon. These
# patterns catch the explicit references that ARE the entities here — ticket ids,
# migration numbers, environments, roles, tables, files — so a fact with any such
# reference gets entities even when GLiNER2 extracts nothing.
_DETERMINISTIC_ENTITY_RE = re.compile(
    r"(ARC-\d+"                                  # tickets
    r"|PR ?#?\d+"                                # PRs
    r"|\bmigration \d{3}\b|\b0\d{2}\b"           # migration numbers
    r"|\b(?:dev|demo|prod|stage|staging)\b"      # environments
    r"|demo-green|dba[- ]?credential|migrator|owner-grant|data_classification"
    r"|role_capabilities|privileged_role_register|rls_auto_enable|ensure_rls"
    r"|\barchen_migrator(?:_\w+)?\b|\barchen-dba(?:-\w+)?\b"
    r"|\b\w+\.(?:py|sql|jsx|js|ts|tsx|md|yml|yaml|json)\b"  # files
    r"|\b[a-z_]+_role\b|\bRLS\b|\bSSM\b|\bIAM\b"            # roles/infra
    r"|ANTHROPIC|OPENAI|Stripe|sk_test|sk_live"            # keys/vendors
    r")", re.IGNORECASE)


def deterministic_entities(body: str) -> list[str]:
    """Explicit-reference entities from a body (surface forms, deduped)."""
    seen = {}
    for m in _DETERMINISTIC_ENTITY_RE.finditer(body or ""):
        s = m.group(0).strip()
        seen.setdefault(s.lower(), s)   # keep first surface form per lower key
    return list(seen.values())


def canonical_key(span: str) -> str:
    """Deterministic canonical key for span->entity resolution."""
    s = span.strip().lower()
    s = _ARTICLES.sub("", s)
    # token-normalize: split on non-word, drop entity-type stopwords, rejoin.
    toks = [t for t in _NONWORD.split(s) if t and t not in _ENTITY_STOPWORDS]
    return "".join(toks)  # e.g. "the 088 migration" -> "088", "email-intelligence" -> "emailintelligence"


def _upsert_entity(cur, key: str, label: str | None, display: str) -> int:
    cur.execute(
        """
        INSERT INTO kg_entities (canonical_key, label, display_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (canonical_key) DO UPDATE SET
            label = COALESCE(kg_entities.label, EXCLUDED.label)
        RETURNING id
        """,
        (key, label, display),
    )
    return cur.fetchone()[0]


def _add_mention(cur, entity_id: int, node_id: int, surface: str) -> None:
    cur.execute(
        """
        INSERT INTO kg_entity_mentions (entity_id, node_id, surface)
        VALUES (%s, %s, %s)
        ON CONFLICT (entity_id, node_id) DO NOTHING
        """,
        (entity_id, node_id, surface),
    )


def _add_entity_edge(cur, src: int, dst: int, relation: str, prov: int | None) -> None:
    cur.execute(
        """
        INSERT INTO kg_entity_edges (src_entity_id, dst_entity_id, relation, provenance_memory_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (src_entity_id, dst_entity_id, relation, provenance_memory_id) DO NOTHING
        """,
        (src, dst, relation, prov),
    )


def build(limit: int | None = None, only_unmarked: bool = True) -> dict[str, Any]:
    """Extract relations from active KG node bodies, build the entity graph.
    only_unmarked=True (default): process only gliner_done_at IS NULL nodes and
    NO-OP (without loading the model) when there are none — so re-runs are cheap
    and incremental. Pass only_unmarked=False to force a full re-extraction."""
    kg = store.kg_conn()
    marker = " AND gliner_done_at IS NULL" if only_unmarked else ""
    # No-op guard (DIFF-gate #6): count work BEFORE loading GLiNER2 (~seconds+VRAM).
    with kg.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM kg_nodes WHERE active AND body <> ''{marker}")
        todo = cur.fetchone()[0]
    if todo == 0:
        kg.close()
        return {"nodes_with_triples": 0, "note": "no unmarked nodes — GLiNER no-op (model not loaded)"}
    with kg.cursor() as cur:
        cur.execute(
            f"SELECT id, kind, memory_id, body FROM kg_nodes "
            f"WHERE active AND body <> ''{marker} ORDER BY id" + (f" LIMIT {int(limit)}" if limit else "")
        )
        nodes = cur.fetchall()

    n_nodes = 0
    n_entities = 0
    n_edges = 0
    n_mentions = 0
    n_det_nodes = 0
    n_deferred = 0
    for node_id, kind, memory_id, body in nodes:
        # Per-node fault isolation (DIFF-gate #4): a mid-run OOM on one node must
        # DEFER the rest (leave gliner_done_at NULL so the next run retries), not
        # crash the whole extraction.
        try:
            triples: list[Triple] = extract(body, source_memory_id=memory_id)
        except Exception as exc:  # e.g. CUDA OOM if the embedder expanded mid-run
            print(f"[build] node {node_id} extract failed ({exc}); deferring rest",
                  file=__import__("sys").stderr)
            n_deferred += 1
            break  # stop cleanly; unmarked nodes retry next run
        # Deterministic entities ALWAYS (coverage fix) — these link facts by
        # shared explicit references (ticket/migration/env/role) even when
        # GLiNER2 extracts nothing, closing the ~42% zero-entity gap.
        det_ents = deterministic_entities(body)
        n_nodes += 1
        with kg.cursor() as cur:
            for t in triples:
                hkey, tkey = canonical_key(t.head), canonical_key(t.tail)
                if not hkey or not tkey or hkey == tkey:
                    continue
                hid = _upsert_entity(cur, hkey, None, t.head)
                tid = _upsert_entity(cur, tkey, None, t.tail)
                _add_mention(cur, hid, node_id, t.head)
                _add_mention(cur, tid, node_id, t.tail)
                _add_entity_edge(cur, hid, tid, t.relation, memory_id)
                n_edges += 1
                n_mentions += 2
            for surface in det_ents:
                dkey = canonical_key(surface)
                if not dkey:
                    continue
                did = _upsert_entity(cur, dkey, "reference", surface)
                _add_mention(cur, did, node_id, surface)
                n_mentions += 1
            if det_ents:
                n_det_nodes += 1
            # Stamp BOTH markers: this pass did GLiNER (even if 0 triples) AND the
            # deterministic entities, so the node is fully extracted.
            cur.execute("UPDATE kg_nodes SET gliner_done_at = NOW(), "
                        "det_done_at = COALESCE(det_done_at, NOW()) WHERE id = %s", (node_id,))
        kg.commit()

    with kg.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_entities")
        n_entities = cur.fetchone()[0]
    report = {"nodes_processed": n_nodes, "entities": n_entities,
              "entity_edges_written": n_edges, "mentions_written": n_mentions,
              "deferred_on_error": n_deferred}
    kg.close()
    return report


def build_deterministic_only(only_unmarked: bool = False) -> dict[str, Any]:
    """Add ONLY the deterministic-reference entities + mentions (no GLiNER2 —
    fast, no GPU/model). Links facts sharing an explicit ticket/migration/env/role
    reference. Idempotent. When only_unmarked=True (the sync path), process only
    nodes with det_done_at IS NULL and stamp det_done_at when done (per-layer
    marker) — so re-runs no-op cheaply and it never re-scans the whole corpus."""
    kg = store.kg_conn()
    where = "WHERE active AND body <> ''" + (" AND det_done_at IS NULL" if only_unmarked else "")
    with kg.cursor() as cur:
        cur.execute(f"SELECT id, memory_id, body FROM kg_nodes {where}")
        nodes = cur.fetchall()
    n_mentions = 0
    n_nodes = 0
    for node_id, memory_id, body in nodes:
        ents = deterministic_entities(body)
        with kg.cursor() as cur:
            for surface in ents:
                dkey = canonical_key(surface)
                if not dkey:
                    continue
                did = _upsert_entity(cur, dkey, "reference", surface)
                _add_mention(cur, did, node_id, surface)
                n_mentions += 1
            if only_unmarked:
                # stamp the layer marker even for nodes with no det entities, so
                # they don't get re-scanned every sync (a no-op node IS "done").
                cur.execute("UPDATE kg_nodes SET det_done_at = NOW() WHERE id = %s", (node_id,))
        kg.commit()
        n_nodes += 1
    with kg.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_entities")
        n_ent = cur.fetchone()[0]
    kg.close()
    return {"nodes_processed": n_nodes, "deterministic_mentions_added": n_mentions,
            "total_entities": n_ent}


if __name__ == "__main__":  # pragma: no cover
    import pprint, sys
    if len(sys.argv) > 1 and sys.argv[1] == "det":
        pprint.pprint(build_deterministic_only())
    else:
        lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
        pprint.pprint(build(limit=lim))
