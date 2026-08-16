"""Five-step write gate per Windsurf synthesis §4.4.

Every write passes this gauntlet before landing in a typed table:

    1. Type declared and valid.        (caller-supplied)
    2. Severity valid (rules only).
    3. Headline present, <=15 words.   (hard limit)
    4. Atomicity check.                (heuristic on length + semantic density)
    5. Duplicate detection.            (cosine similarity against same-kind)

Merge is an invalid operation for RULE type. Full stop.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import (
    DUPLICATE_COSINE_THRESHOLD,
    SIMILAR_RULE_COSINE,
    HEADLINE_WORD_CAP,
    BODY_WORD_CAP,
)

VALID_KINDS = ("rule", "fact", "incident", "task")
VALID_SEVERITIES = ("BLOCKER", "PATTERN")  # DEPRECATED is set by supersede, not by callers


class WriteGateError(ValueError):
    """Write was rejected at the gate. Message explains which step."""


@dataclass
class DuplicateMatch:
    memory_id: int
    similarity: float
    headline: str


@dataclass
class NeighborMatch:
    memory_id: int
    similarity: float
    headline: str
    severity: str | None
    project: str


@dataclass
class GateResult:
    """Outcome of the single neighbor scan: the top match as a DuplicateMatch
    (only when >= dup threshold), plus in-band near neighbors (similar but not
    duplicate) for the write-time 'consider supersede' hint."""
    duplicate: DuplicateMatch | None
    neighbors: list[NeighborMatch]


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
            f"step 2 (severity): rule severity must be one of {VALID_SEVERITIES}, got {severity!r}"
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
    """Heuristic: a single rule/fact fits in <= ~BODY_WORD_CAP words AND
    contains at most one occurrence of an explicit conjunction-of-rules marker.

    Not a perfect check — nothing is — but it catches the most common
    walls-of-text failure mode empirically seen in v1's corpus.
    """
    if not body or not body.strip():
        raise WriteGateError("step 4 (atomicity): body required")

    words = body.split()
    hard_ceiling = BODY_WORD_CAP * 2
    if len(words) > hard_ceiling:
        raise WriteGateError(
            f"step 4 (atomicity): body has {len(words)} words (>{hard_ceiling}, "
            f"hard ceiling = 2x BODY_WORD_CAP={BODY_WORD_CAP}). "
            "This is almost certainly multiple atomic memories bundled together. "
            "Split before storing."
        )

    markers = ("GUARDRAIL 2026-", "GUARDRAIL 2025-", "\nUpdate:", "\nUPDATE:")
    found = sum(body.count(m) for m in markers)
    if found >= 2:
        raise WriteGateError(
            f"step 4 (atomicity): body contains {found} date-stamped GUARDRAIL/Update "
            "markers — this is the v1 merge pathology. Create one atomic memory per "
            "rule and use supersede() to revise."
        )


def _scan_neighbors(conn, kind: str, embedding_vec: str, k: int,
                    project: str | None = None) -> list[tuple]:
    """One cosine query returning the top-k nearest active same-kind rows.
    (Sequential scan: memory_index has no vector index — pgvector's HNSW caps at
    2000d and the embedding is 4096d, see schema.py.) Fetches severity + project.
    Returns rows of (memory_id, headline, severity, project, similarity).

    If `project` is given, restricts to that project OR global ('') — used for the
    similar-rule hint so its k budget is spent on eligible rows (a dense corpus of
    OTHER-project topical twins can't crowd out the same-project neighbor). Pass
    project=None for the cross-project duplicate check.
    """
    proj_clause = ""
    params: list = [embedding_vec, kind]
    if project is not None:
        proj_clause = "AND (project = %s OR project = '')"
        params.append(project)
    params += [embedding_vec, k]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT memory_id, headline, severity, project,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM memory_index
            WHERE kind = %s AND active = TRUE
            {proj_clause}
            ORDER BY embedding <=> %s::vector ASC
            LIMIT %s
            """,
            tuple(params),
        )
        return cur.fetchall()


def find_duplicate(conn, kind: str, embedding_vec: str, threshold: float = DUPLICATE_COSINE_THRESHOLD) -> DuplicateMatch | None:
    """Cosine similarity against same-kind active entries. Returns the
    top match if similarity exceeds `threshold`, else None.

    Note: duplicate detection is intentionally CROSS-PROJECT (no project filter) —
    the same rule written under two projects is still a duplicate."""
    rows = _scan_neighbors(conn, kind, embedding_vec, 1)
    if not rows:
        return None
    memory_id, headline, _severity, _project, similarity = rows[0]
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
    """Run all five steps. Returns a DuplicateMatch if step 5 flagged one;
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


def run_gate_with_neighbors(
    conn,
    *,
    kind: str,
    headline: str,
    body: str,
    severity: str | None = None,
    embedding_vec: str | None = None,
    project: str = "",
    similar_lo: float = SIMILAR_RULE_COSINE,
    k: int = 5,
) -> GateResult:
    """Like run_gate, but derives BOTH the duplicate check and the in-band
    'similar existing rule' neighbors from ONE sequential scan (top-k). Used by
    remember_rule to attach a non-blocking supersede hint at no extra scan cost.

    The duplicate is cross-project (matches find_duplicate). The similar-rule
    HINT is scoped to same-project-or-global, so cross-project topical twins are
    not surfaced as spurious hints.
    """
    check_kind(kind)
    check_severity(kind, severity)
    check_headline(headline)
    check_atomicity(body)
    if embedding_vec is None:
        return GateResult(duplicate=None, neighbors=[])

    # Two scoped scans (each an O(N) sequential scan; no re-embed — both reuse the
    # already-computed embedding_vec). We cannot serve both scopes from one scan:
    #  - the DUPLICATE check must stay CROSS-project (a rule duplicated across two
    #    projects is still a duplicate);
    #  - the HINT must be SAME-project/global, and its top-k budget must be spent on
    #    eligible rows or a dense corpus of other-project twins would crowd out the
    #    real same-project neighbor.
    dup_rows = _scan_neighbors(conn, kind, embedding_vec, 1)  # cross-project top-1
    duplicate: DuplicateMatch | None = None
    if dup_rows:
        d_id, d_h, _sev, _proj, d_sim = dup_rows[0]
        if float(d_sim) >= DUPLICATE_COSINE_THRESHOLD:
            duplicate = DuplicateMatch(memory_id=d_id, similarity=float(d_sim), headline=d_h)

    neighbors: list[NeighborMatch] = []
    if duplicate is None:
        # Only look for hint neighbors when this isn't a duplicate write.
        for memory_id, h, sev, proj, sim in _scan_neighbors(
                conn, kind, embedding_vec, k, project=project):
            sim = float(sim)
            if similar_lo <= sim < DUPLICATE_COSINE_THRESHOLD:
                neighbors.append(NeighborMatch(
                    memory_id=memory_id, similarity=sim, headline=h,
                    severity=sev, project=proj))
    return GateResult(duplicate=duplicate, neighbors=neighbors)
