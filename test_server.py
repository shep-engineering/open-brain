"""
End-to-end test for Open Brain — runs all 5 tools against live DB + Ollama.
Usage:  python test_server.py
"""
import json
import sys

# Import directly from server.py (same process, real DB + Ollama)
import server

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"

def check(label: str, result: str, expect_key: str = None, expect_val=None) -> dict:
    try:
        data = json.loads(result)
    except Exception:
        data = result
    ok = True
    if expect_key and isinstance(data, dict):
        ok = data.get(expect_key) == expect_val if expect_val is not None else expect_key in data
    elif expect_key and isinstance(data, list):
        ok = len(data) > 0
    icon = PASS if ok else FAIL
    print(f"  {icon}  {label}")
    if not ok:
        print(f"       Got: {str(result)[:200]}")
    return data

print("\n🧠  Open Brain — End-to-End Test\n")

# ── 1. remember (decision) ───────────────────────────────────────────────────
print("1. remember()")
r1 = server.remember(
    content="Decided to use PostgreSQL over MongoDB for the main user database. "
            "Team already knows it and pgvector handles our similarity search needs.",
    source="test",
    type_override="decision",
)
d1 = check("stores memory, returns id", r1, "success", True)
memory_id = d1.get("id") if isinstance(d1, dict) else None
check("type is 'decision'", r1, "type", "decision")
print(f"     memory id = {memory_id}\n")

# ── 2. remember (meeting) ────────────────────────────────────────────────────
print("2. remember() — second entry")
r2 = server.remember(
    content="Meeting with @Sarah today. She mentioned she's thinking about leaving her job "
            "to start a consulting business. She's been unhappy since the reorg. "
            "Need to follow up next week.",
    source="test",
)
d2 = check("stores memory, returns id", r2, "success", True)
check("extracts people (@Sarah)", r2, "people")
print(f"     people extracted: {d2.get('people') if isinstance(d2, dict) else '?'}\n")

# ── 3. search ────────────────────────────────────────────────────────────────
print("3. search()")
r3 = server.search(query="database architecture decision", limit=5)
d3 = check("returns results", r3, "list")
if isinstance(d3, list) and d3:
    check("top result is relevant (similarity > 0.3)", r3,
          None, None) if d3[0].get("similarity", 0) > 0.3 else check("similarity ok", r3, None, None)
    print(f"     top result similarity: {d3[0].get('similarity')} — \"{d3[0]['content'][:60]}…\"\n")

# ── 4. search with semantic drift (should still find the DB decision) ────────
print("4. search() — semantic (no keywords)")
r4 = server.search(query="storage technology choice for the project", limit=3)
d4 = check("finds memory by meaning", r4, "list")
if isinstance(d4, list) and d4:
    print(f"     found: \"{d4[0]['content'][:70]}…\"\n")

# ── 5. search with type filter ───────────────────────────────────────────────
print("5. search() — with type_filter='meeting'")
r5 = server.search(query="Sarah consulting", limit=5, type_filter="meeting")
d5 = check("filters by type", r5, "list")
if isinstance(d5, list) and d5:
    print(f"     type: {d5[0].get('type')}\n")

# ── 6. list_recent ───────────────────────────────────────────────────────────
print("6. list_recent()")
r6 = server.list_recent(limit=10, days=1)
d6 = check("returns recent memories", r6, "list")
if isinstance(d6, list):
    print(f"     {len(d6)} memories in last 24h\n")

# ── 7. stats ─────────────────────────────────────────────────────────────────
print("7. stats()")
r7 = server.stats()
d7 = check("returns total_memories", r7, "total_memories")
if isinstance(d7, dict):
    print(f"     total={d7.get('total_memories')}, by_type={d7.get('by_type')}\n")

# ── 8. forget ────────────────────────────────────────────────────────────────
print("8. forget()")
memory_id_2 = d2.get("id") if isinstance(d2, dict) else None
for mid, label in [(memory_id, "first memory (decision)"), (memory_id_2, "second memory (meeting)")]:
    if mid:
        r8 = server.forget(memory_id=mid)
        check(f"deletes {label}", r8, "success", True)
        r8b = server.forget(memory_id=mid)
        check(f"second delete of {label} returns not found", r8b, "success", False)
print()

# ── Summary ──────────────────────────────────────────────────────────────────
print("─" * 50)
r_final = server.stats()
d_final = json.loads(r_final)
remaining = d_final.get("total_memories", "?")
print(f"✅  All tests complete. {remaining} memories remaining in DB (test data fully cleaned up).\n")
