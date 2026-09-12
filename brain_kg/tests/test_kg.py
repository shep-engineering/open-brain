"""brain_kg tests — graph construction + retrieval logic against a hermetic KG DB.

These do NOT touch the brain. They build a tiny in-memory-ish graph in the KG DB
(or a throwaway DB) and assert the correction + traversal behavior directly.

Run: pytest brain_kg/tests/test_kg.py
"""
from __future__ import annotations

import os

import psycopg2
import pytest

from brain_kg import store
from brain_kg.schema import apply_schema
from brain_kg import retrieve


TEST_DB_URL = os.getenv(
    "OPEN_BRAIN_KG_TEST_DATABASE_URL",
    "postgresql://postgres:password@localhost:5433/open_brain_kg_test",
)


def _fresh_test_db():
    """Create a throwaway KG test DB (separate from the real KG DB)."""
    admin = psycopg2.connect("postgresql://postgres:password@localhost:5433/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS open_brain_kg_test")
        cur.execute("CREATE DATABASE open_brain_kg_test")
    admin.close()
    conn = psycopg2.connect(TEST_DB_URL)
    apply_schema(conn)
    return conn


@pytest.fixture()
def kg():
    try:
        conn = _fresh_test_db()
    except psycopg2.Error as e:  # pragma: no cover
        pytest.skip(f"KG Postgres not reachable: {e}")
    yield conn
    conn.close()


def _n(conn, kind, mem, headline, active=True, emb=None):
    return store.upsert_node(conn, kind=kind, memory_id=mem, project="t",
                             headline=headline, body=headline,
                             embedding_literal=emb, active=active, source="test")


def test_supersede_edge_marks_retired_inactive_and_walk_reaches_corrector(kg):
    # 4096-dim unit-ish vectors: two near-identical, one far.
    dims = 4096
    v_a = "[" + ",".join(["0.1"] * dims) + "]"
    v_b = "[" + ",".join(["0.1"] * dims) + "]"      # identical to a -> seed will find it
    retired = _n(kg, "rule", 100, "old rule", active=False, emb=v_a)
    corrector = _n(kg, "rule", 101, "new rule", active=True, emb=v_b)
    store.add_edge(kg, src_id=corrector, dst_id=retired, relation="supersedes",
                   weight=1.0, provenance_memory_id=101)
    kg.commit()

    # Query near the retired rule's vector; graph must surface the CORRECTOR (101),
    # never the inactive retired node (100) as a live hit.
    res = retrieve.graph_recall(kg, v_a, k=5)
    keys = [r["key"] for r in res]
    assert "rule:101" in keys           # corrector reachable
    # retired is active=FALSE so seed excludes it
    assert "rule:100" not in [r["key"] for r in res if r["via"] == "seed"]


def test_seed_excludes_inactive_nodes(kg):
    dims = 4096
    v = "[" + ",".join(["0.2"] * dims) + "]"
    _n(kg, "fact", 200, "stale fact", active=False, emb=v)
    _n(kg, "fact", 201, "current fact", active=True, emb=v)
    kg.commit()
    seed = retrieve.engine_a_seed(kg, v, k=5)
    keys = [r["key"] for r in seed]
    assert "fact:201" in keys
    assert "fact:200" not in keys       # inactive excluded from seed


def test_graph_recall_returns_exactly_k(kg):
    dims = 4096
    v = "[" + ",".join(["0.3"] * dims) + "]"
    for i in range(20):
        _n(kg, "fact", 300 + i, f"fact {i}", active=True, emb=v)
    kg.commit()
    res = retrieve.graph_recall(kg, v, k=5)
    assert len(res) == 5                # never more than K (no volume win)


def test_neighbor_edge_reaches_connected_node_outside_topk(kg):
    dims = 4096
    # seed vector matches node 400 exactly; node 401 is a bit different (won't be
    # top-1 by itself) but is linked as a neighbor of 400.
    v_seed = "[" + ",".join(["0.5"] * dims) + "]"
    v_far = "[" + ",".join(["0.49"] * dims) + "]"
    a = _n(kg, "fact", 400, "seed fact", active=True, emb=v_seed)
    b = _n(kg, "fact", 401, "connected fact", active=True, emb=v_far)
    # add many distractors so 401 would fall outside a k=1 seed
    for i in range(10):
        _n(kg, "fact", 500 + i, f"distractor {i}", active=True, emb=v_far)
    store.add_edge(kg, src_id=a, dst_id=b, relation="neighbor", weight=0.99,
                   provenance_memory_id=None)
    kg.commit()
    res = retrieve.graph_recall(kg, v_seed, k=3, seed_k=1, hops=1)
    keys = [r["key"] for r in res]
    assert "fact:401" in keys           # reached via the neighbor edge, not seed
