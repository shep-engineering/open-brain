#!/usr/bin/env python3
"""
Open Brain — Agent-readable second brain MCP server.

One PostgreSQL database. One MCP server. Every AI you use.

Usage:
    python server.py

MCP client config:
    { "command": "python", "args": ["C:/Users/DAVE/CascadeProjects/open-brain/server.py"] }
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

DATABASE_URL       = os.getenv("DATABASE_URL",           "postgresql://postgres:password@localhost:5432/openbrain")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER",     "ollama")
EMBEDDING_DIMS     = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL",        "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY",         "")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
METADATA_LLM_MODEL = os.getenv("METADATA_LLM_MODEL",    "")
DEDUP_THRESHOLD    = float(os.getenv("DEDUP_THRESHOLD",  "0.92"))

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _to_vec(v: list[float]) -> str:
    return "[" + ",".join(map(str, v)) + "]"


def _normalize_row(row: dict) -> dict:
    """Convert psycopg2 types (datetime, Decimal) to JSON-safe equivalents."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


# ─── Embeddings ───────────────────────────────────────────────────────────────

def _http_post(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode()
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach {url}: {e.reason}") from e


def _embed_ollama(text: str) -> list[float]:
    result = _http_post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        {"model": OLLAMA_EMBED_MODEL, "prompt": text},
    )
    if "embedding" not in result:
        raise RuntimeError(
            f"Ollama returned no embedding. "
            f"Pull the model first:  ollama pull {OLLAMA_EMBED_MODEL}"
        )
    return result["embedding"]


def _embed_openai(text: str) -> list[float]:
    body: dict = {"model": OPENAI_EMBED_MODEL, "input": text}
    if EMBEDDING_DIMS:
        body["dimensions"] = EMBEDDING_DIMS
    result = _http_post(
        "https://api.openai.com/v1/embeddings",
        body,
        {"Authorization": f"Bearer {OPENAI_API_KEY}"},
    )
    return result["data"][0]["embedding"]


def get_embedding(text: str) -> list[float]:
    if EMBEDDING_PROVIDER == "openai":
        return _embed_openai(text)
    return _embed_ollama(text)


# ─── Metadata Extraction ──────────────────────────────────────────────────────

VALID_TYPES = {
    "decision", "idea", "meeting", "person", "insight",
    "task", "journal", "reference", "note",
}
_STOP = {
    "The", "This", "That", "They", "There", "These", "Those",
    "When", "Where", "What", "Which", "While", "After", "Before",
    "And", "But", "For", "Not", "With", "From", "Into", "Also",
}


def _meta_heuristic(text: str) -> dict:
    lower = text.lower()

    people = list(set(re.findall(r"@([\w][\w ]{0,20}[\w]|[\w]+)", text)))
    tags   = list(set(re.findall(r"#(\w+)", text)))

    type_ = "note"
    if   re.search(r"\b(decided|decision|going with|chose|we chose|will go with)\b", lower):   type_ = "decision"
    elif re.search(r"\b(meeting|met with|talked with|spoke with|call with|standup|catchup)\b", lower): type_ = "meeting"
    elif re.search(r"\b(idea|what if|could we|might work|brainstorm|thinking about building)\b", lower): type_ = "idea"
    elif re.search(r"\b(todo|action item|need to|must|follow up|remind me)\b", lower):          type_ = "task"
    elif re.search(r"\b(learned|realized|insight|key takeaway|noticed that|turns out)\b", lower): type_ = "insight"
    elif re.search(r"\b(works at|currently at|her background|his background)\b", lower):        type_ = "person"
    elif re.search(r"\b(journal|reflecting|today i|feeling|grateful)\b", lower):                type_ = "journal"

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    action_items = [
        s for s in sentences
        if re.search(r"\b(need to|must|should|todo|follow up|action|will|remind|schedule|send|review|check)\b", s, re.I)
    ][:5]

    raw_topics = re.findall(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b", text)
    topics = list(set(t for t in raw_topics if t.split()[0] not in _STOP))[:8]

    return {"type": type_, "people": people, "topics": topics, "action_items": action_items, "tags": tags}


def _meta_llm(text: str) -> dict:
    prompt = (
        "Extract metadata from this note. Reply ONLY with valid JSON, no markdown.\n\n"
        f'Note: "{text}"\n\n'
        'JSON: {"type":"decision|idea|meeting|person|insight|task|journal|reference|note",'
        '"people":[],"topics":[],"action_items":[],"tags":[]}'
    )
    result = _http_post(
        f"{OLLAMA_BASE_URL}/api/generate",
        {"model": METADATA_LLM_MODEL, "prompt": prompt, "stream": False, "format": "json"},
    )
    parsed = json.loads(result["response"])

    def _list(v: object) -> list:
        return v if isinstance(v, list) else []

    return {
        "type":         parsed.get("type", "note") if parsed.get("type") in VALID_TYPES else "note",
        "people":       _list(parsed.get("people")),
        "topics":       _list(parsed.get("topics")),
        "action_items": _list(parsed.get("action_items")),
        "tags":         _list(parsed.get("tags")),
    }


def extract_metadata(text: str) -> dict:
    if METADATA_LLM_MODEL:
        try:
            return _meta_llm(text)
        except Exception:
            pass
    return _meta_heuristic(text)


# ─── Database ─────────────────────────────────────────────────────────────────

_conn: psycopg2.extensions.connection | None = None


def _get_conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(DATABASE_URL)
        _conn.autocommit = True
    return _conn


def db_store(content: str, embedding: list[float], metadata: dict) -> dict:
    conn = _get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO memories (content, embedding, metadata) VALUES (%s, %s::vector, %s) "
            "RETURNING id, content, metadata, created_at",
            (content, _to_vec(embedding), json.dumps(metadata)),
        )
        return _normalize_row(dict(cur.fetchone()))  # type: ignore[arg-type]


def db_find_duplicate(embedding: list[float], threshold: float = DEDUP_THRESHOLD) -> dict | None:
    """Return the closest existing memory if similarity >= threshold, else None."""
    conn = _get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, content, metadata, created_at,
                   round((1 - (embedding <=> %s::vector))::numeric, 4) AS similarity
            FROM memories
            ORDER BY embedding <=> %s::vector
            LIMIT 1
            """,
            (_to_vec(embedding), _to_vec(embedding)),
        )
        row = cur.fetchone()
        if row and float(row["similarity"]) >= threshold:
            return _normalize_row(dict(row))
    return None


def db_update(memory_id: int, content: str, embedding: list[float], metadata: dict) -> dict:
    """Update an existing memory's content, embedding, and metadata."""
    conn = _get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE memories SET content = %s, embedding = %s::vector, metadata = %s "
            "WHERE id = %s RETURNING id, content, metadata, created_at",
            (content, _to_vec(embedding), json.dumps(metadata), memory_id),
        )
        return _normalize_row(dict(cur.fetchone()))


def db_store_deduped(content: str, embedding: list[float], metadata: dict) -> tuple[dict, str]:
    """Store a memory with dedup. Returns (memory_dict, action) where action is 'stored', 'updated', or 'skipped'."""
    existing = db_find_duplicate(embedding)
    if existing is None:
        return db_store(content, embedding, metadata), "stored"

    # Duplicate found. If new content is longer (more detailed), update it.
    if len(content) > len(existing["content"]):
        memory = db_update(existing["id"], content, embedding, metadata)
        return memory, "updated"

    # Existing is already as good or better; skip.
    return existing, "skipped"


def db_search(
    query_vec: list[float],
    limit: int,
    type_filter: str | None,
    people_filter: list[str] | None,
) -> list[dict]:
    conn = _get_conn()
    conditions: list[str] = []
    extra_params: list = []

    if type_filter:
        extra_params.append(type_filter)
        conditions.append("metadata->>'type' = %s")

    if people_filter:
        person_conds = []
        for person in people_filter:
            extra_params.append(person)
            person_conds.append("metadata->'people' ? %s")
        conditions.append(f"({' OR '.join(person_conds)})")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    # Vec appears once (in CTE), filter params in the middle, limit at end
    params = [_to_vec(query_vec)] + extra_params + [limit]

    sql = f"""
        WITH q AS (
            SELECT id, content, metadata, created_at,
                   (embedding <=> %s::vector) AS dist
            FROM memories
            {where}
        )
        SELECT id, content, metadata, created_at,
               round((1 - dist)::numeric, 4) AS similarity
        FROM q
        ORDER BY dist
        LIMIT %s
    """

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [_normalize_row(dict(r)) for r in cur.fetchall()]


def db_list_recent(limit: int, days: int | None) -> list[dict]:
    conn = _get_conn()
    params: list = []
    date_filter = ""
    if days:
        params.append(days)
        date_filter = "WHERE created_at > NOW() - INTERVAL '1 day' * %s"
    params.append(limit)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT id, content, metadata, created_at FROM memories "
            f"{date_filter} ORDER BY created_at DESC LIMIT %s",
            params,
        )
        return [_normalize_row(dict(r)) for r in cur.fetchall()]


def db_stats() -> dict:
    conn = _get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM memories")
        total = cur.fetchone()["cnt"]  # type: ignore[index]

        cur.execute(
            "SELECT COALESCE(metadata->>'type', 'note') AS type, COUNT(*) AS cnt "
            "FROM memories GROUP BY metadata->>'type' ORDER BY cnt DESC"
        )
        by_type = {r["type"]: r["cnt"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) AS cnt FROM memories WHERE created_at > NOW() - INTERVAL '7 days'")
        r7 = cur.fetchone()["cnt"]  # type: ignore[index]

        cur.execute("SELECT COUNT(*) AS cnt FROM memories WHERE created_at > NOW() - INTERVAL '30 days'")
        r30 = cur.fetchone()["cnt"]  # type: ignore[index]

    return {"total": total, "by_type": by_type, "recent_7_days": r7, "recent_30_days": r30}


def db_delete(memory_id: int) -> bool:
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM memories WHERE id = %s", (memory_id,))
        return (cur.rowcount or 0) > 0


# ─── MCP Server ───────────────────────────────────────────────────────────────

mcp = FastMCP("open-brain")


@mcp.tool()
def remember(content: str, source: str = "", type_override: str = "") -> str:
    """Store a thought, note, decision, or information in your brain.

    Auto-detects type (decision/idea/meeting/task/etc), extracts people, topics,
    and action items. Creates a semantic embedding so it can be retrieved by
    meaning from any AI tool connected via MCP — Claude, Cursor, ChatGPT, etc.

    Args:
        content: The thought, note, or information to remember.
        source: Where captured from (e.g. 'cursor', 'slack', 'cli').
        type_override: Override auto-detected type:
            decision | idea | meeting | person | insight | task | journal | reference | note
    """
    try:
        embedding = get_embedding(content)
        metadata  = extract_metadata(content)
        if type_override:
            metadata["type"] = type_override
        if source:
            metadata["source"] = source
        memory, action = db_store_deduped(content, embedding, metadata)
        return json.dumps({
            "success":      True,
            "action":       action,
            "id":           memory["id"],
            "type":         metadata["type"],
            "people":       metadata.get("people", []),
            "topics":       metadata.get("topics", []),
            "action_items": metadata.get("action_items", []),
            "stored_at":    memory["created_at"],
        }, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


@mcp.tool()
def search(
    query: str,
    limit: int = 10,
    type_filter: str = "",
    people_filter: Optional[list[str]] = None,
) -> str:
    """Semantically search your brain by meaning — not just keywords.

    Finds thoughts, decisions, and notes even without exact words.
    Works across everything ever captured, from any AI tool.

    Args:
        query: What to search for — describe by meaning, not exact keywords.
        limit: Max results to return (default 10, max 50).
        type_filter: Filter by type:
            decision | idea | meeting | person | insight | task | journal | reference | note
        people_filter: Filter to memories mentioning specific people.
    """
    try:
        embedding = get_embedding(query)
        memories  = db_search(embedding, min(limit, 50), type_filter or None, people_filter)
        if not memories:
            return "No memories found matching that query."
        meta = lambda m: m["metadata"] if isinstance(m["metadata"], dict) else {}  # noqa: E731
        return json.dumps([{
            "id":           m["id"],
            "content":      m["content"],
            "similarity":   float(m.get("similarity") or 0),
            "type":         meta(m).get("type"),
            "people":       meta(m).get("people", []),
            "topics":       meta(m).get("topics", []),
            "action_items": meta(m).get("action_items", []),
            "created_at":   str(m["created_at"]),
        } for m in memories], indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_recent(limit: int = 20, days: int = 0) -> str:
    """Browse your most recent captures.

    Useful for reviewing what you've been thinking about lately.

    Args:
        limit: Max memories to return (default 20, max 100).
        days:  Only show memories from the last N days (0 = all time).
    """
    try:
        memories = db_list_recent(min(limit, 100), days if days > 0 else None)
        if not memories:
            return "No memories yet. Use `remember` to start building your brain."
        meta = lambda m: m["metadata"] if isinstance(m["metadata"], dict) else {}  # noqa: E731
        return json.dumps([{
            "id":         m["id"],
            "preview":    (m["content"][:150] + "…") if len(m["content"]) > 150 else m["content"],
            "type":       meta(m).get("type"),
            "people":     meta(m).get("people", [])[:3],
            "created_at": str(m["created_at"]),
        } for m in memories], indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def stats() -> str:
    """Get statistics about your brain: total memories, by type, recent activity."""
    try:
        s = db_stats()
        return json.dumps({
            "total_memories": s["total"],
            "by_type":        s["by_type"],
            "last_7_days":    s["recent_7_days"],
            "last_30_days":   s["recent_30_days"],
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def capture_context(context: str, source: str = "") -> str:
    """Automatically extract and store memories from raw conversation or session context.

    THIS is the primary tool for automatic brain capture. AI agents should call
    this on their own at natural checkpoints — after completing a task, when a
    decision is made, when something notable about the user or project is learned.
    Do NOT wait for the user to ask. The user should never have to say "remember this."

    The brain will decompose the context into individual atomic memories and store
    each one separately for precise future retrieval.

    When to call automatically (without being asked):
    - A coding task or feature is completed
    - A technical decision is made (architecture, library, approach)
    - A bug is diagnosed and fixed (cause + fix)
    - Something about the user's preferences, workflow, or project is learned
    - A meeting, discussion, or planning session occurs
    - An error or blocker is encountered and resolved

    Args:
        context: Raw text to capture — conversation excerpt, session summary,
                 decisions made, things learned. Can be long, dump freely.
        source:  Which agent is capturing (e.g. 'windsurf', 'cursor', 'claude').
    """
    try:
        stored = []
        errors = []

        # Try LLM extraction to decompose context into atomic memories
        if METADATA_LLM_MODEL:
            try:
                prompt = (
                    "Extract distinct, atomic memories from this context. "
                    "Each memory should be one clear fact, decision, insight, or event — self-contained enough "
                    "to be useful when retrieved alone months from now. "
                    "Only include things worth remembering long-term. Skip filler.\n\n"
                    f"Context:\n{context}\n\n"
                    "Reply ONLY with a JSON array of strings. Example:\n"
                    '["Decided to use Redis for session caching due to TTL support.", '
                    '"User prefers flat file structure over nested src/ directories.", '
                    '"Fixed bug where auth token wasn\'t being refreshed on 401."]'
                )
                result = _http_post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    {"model": METADATA_LLM_MODEL, "prompt": prompt, "stream": False},
                )
                raw = result.get("response", "").strip()
                # Extract JSON array from response
                start = raw.find("[")
                end   = raw.rfind("]") + 1
                if start != -1 and end > start:
                    items = json.loads(raw[start:end])
                    if isinstance(items, list) and items:
                        for item in items:
                            if isinstance(item, str) and item.strip():
                                try:
                                    embedding = get_embedding(item)
                                    meta      = extract_metadata(item)
                                    if source:
                                        meta["source"] = source
                                    meta["auto_captured"] = True
                                    memory, action = db_store_deduped(item, embedding, meta)
                                    stored.append({"id": memory["id"], "preview": item[:100], "type": meta["type"], "action": action})
                                except Exception as e:
                                    errors.append(str(e))
            except Exception:
                pass  # fall through to single-memory fallback

        # Fallback: store whole context as one memory
        if not stored:
            embedding = get_embedding(context)
            meta      = extract_metadata(context)
            if source:
                meta["source"] = source
            meta["auto_captured"] = True
            memory, action = db_store_deduped(context, embedding, meta)
            stored.append({"id": memory["id"], "preview": context[:100], "type": meta["type"], "action": action})

        return json.dumps({
            "success":       True,
            "memories_stored": len(stored),
            "stored":        stored,
            "errors":        errors if errors else None,
        }, indent=2)

    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


@mcp.tool()
def forget(memory_id: int) -> str:
    """Permanently delete a specific memory by its ID.

    Get the ID from the search or list_recent output.

    Args:
        memory_id: The ID of the memory to delete.
    """
    try:
        deleted = db_delete(memory_id)
        return json.dumps({
            "success": deleted,
            "id":      memory_id,
            "message": f"Memory {memory_id} deleted." if deleted else f"Memory {memory_id} not found.",
        })
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
