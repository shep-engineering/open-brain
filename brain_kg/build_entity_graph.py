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


def build(limit: int | None = None) -> dict[str, Any]:
    """Extract relations from every active KG node body, build the entity graph."""
    kg = store.kg_conn()
    with kg.cursor() as cur:
        cur.execute(
            "SELECT id, kind, memory_id, body FROM kg_nodes "
            "WHERE active AND body <> '' ORDER BY id" + (f" LIMIT {int(limit)}" if limit else "")
        )
        nodes = cur.fetchall()

    n_nodes = 0
    n_entities = 0
    n_edges = 0
    n_mentions = 0
    for node_id, kind, memory_id, body in nodes:
        triples: list[Triple] = extract(body, source_memory_id=memory_id)
        if not triples:
            continue
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
        kg.commit()

    with kg.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_entities")
        n_entities = cur.fetchone()[0]
    report = {"nodes_with_triples": n_nodes, "entities": n_entities,
              "entity_edges_written": n_edges, "mentions_written": n_mentions}
    kg.close()
    return report


if __name__ == "__main__":  # pragma: no cover
    import pprint, sys
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    pprint.pprint(build(limit=lim))
