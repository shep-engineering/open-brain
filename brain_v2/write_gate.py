"""Five-step write gate per Windsurf synthesis §4.4.

Every write passes this gauntlet before landing in a typed table:

    1. Type declared and valid.        (caller-supplied)
    2. Atomicity check.                (heuristic on length + semantic density)
    3. Headline present, <=15 words.   (hard limit)
    4. Duplicate detection.            (cosine similarity against same-kind)
    5. Supersede for RULE.             (RULE bodies immutable)

Merge is an invalid operation for RULE type. Full stop.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import DUPLICATE_COSINE_THRESHOLD, HEADLINE_WORD_CAP

VALID_KINDS = ("rule", "fact", "incident", "task")
VALID_SEVERITIES = ("BLOCKER", "PATTERN", "CONTEXT")  # DEPRECATED is set by supersede, not by callers


class WriteGateError(ValueError):
    """Write was rejected at the gate. Message explains which step."""


@dataclass
class DuplicateMatch:
    memory_id: int
    similarity: float
    headline: str


def check_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        raise WriteGateError(
            f"step 1 (type): kind must be one of {VALID_KINDS}, got {kind!r}"
        )


def check_severity(kind: str, severity: str | None) -> None:
    if kind != "rule":
        return
    if severity not in VALID_SEVERITIES:
        raise WriteGateError(
            f"step 1 (type): rule severity must be one of {VALID_SEVERITIES}, got {severity!r}"
        )


def check_headline(headline: str) -> None:
    if not headline or not headline.strip():
        raise WriteGateError("step 3 (headline): required, cannot be empty")
    words = headline.strip().split()
    if len(words) > HEADLINE_WORD_CAP:
        raise WriteGateError(
            f"step 3 (headline): >{HEADLINE_WORD_CAP} words ({len(words)}). "
            f"Shorten or split into two atomic memories."
        )


def check_atomicity(body: str) -> None:
    """Heuristic: a single rule/fact fits in <= ~200 words AND contains at
    most one occurrence of an explicit conjunction-of-rules marker.

    Not a perfect check — nothing is — but it catches the most common
    walls-of-text failure mode empirically seen in v1's corpus.
    """
    if not body or not body.strip():
        raise WriteGateError("step 2 (atomicity): body required")

    words = body.split()
    if len(words) > 400:  # hard ceiling; soft ceiling is 200 but we allow slack
        raise WriteGateError(
            f"step 2 (atomicity): body has {len(words)} words (>400). "
            "This is almost certainly multiple atomic memories bundled together. "
            "Split before storing."
        )

    markers = ("GUARDRAIL 2026-", "GUARDRAIL 2025-", "\nUpdate:", "\nUPDATE:")
    found = sum(body.count(m) for m in markers)
    if found >= 2:
        raise WriteGateError(
            f"step 2 (atomicity): body contains {found} date-stamped GUARDRAIL/Update "
            "markers — this is the v1 merge pathology. Create one atomic memory per "
            "rule and use supersede() to revise."
        )


def find_duplicate(conn, kind: str, embedding_vec: str, threshold: float = DUPLICATE_COSINE_THRESHOLD) -> DuplicateMatch | None:
    """Cosine similarity against same-kind active entries. Returns the
    top match if similarity exceeds `threshold`, else None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT memory_id, headline, 1 - (embedding <=> %s::vector) AS similarity
            FROM memory_index
            WHERE kind = %s AND active = TRUE
            ORDER BY embedding <=> %s::vector ASC
            LIMIT 1
            """,
            (embedding_vec, kind, embedding_vec),
        )
        row = cur.fetchone()
    if not row:
        return None
    memory_id, headline, similarity = row
    if similarity < threshold:
        return None
    return DuplicateMatch(memory_id=memory_id, similarity=float(similarity), headline=headline)


def run_gate(
    conn,
    *,
    kind: str,
    headline: str,
    body: str,
    severity: str | None = None,
    embedding_vec: str | None = None,
) -> DuplicateMatch | None:
    """Run all five steps. Returns a DuplicateMatch if step 4 flagged one;
    caller must route to supersede (RULE) or decide how to handle (FACT/INCIDENT).

    Raises WriteGateError on any step failure.
    """
    check_kind(kind)
    check_severity(kind, severity)
    check_headline(headline)
    check_atomicity(body)
    if embedding_vec is not None:
        return find_duplicate(conn, kind, embedding_vec)
    return None
