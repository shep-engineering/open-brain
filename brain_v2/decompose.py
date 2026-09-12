"""Heuristic text decomposition for capture_context_v2.

Splits raw context into atomic chunks and classifies each as
rule / fact / incident / task. No metadata LLM call — classification
is keyword-based. This keeps the metadata LLM out of the hot path
(infra-cost-addendum §2) while providing the auto-decompose convenience
that makes capture_context the most-used write tool.

Classification accuracy: heuristic, not perfect. The write gate
catches structural violations (atomicity, headline length). False
classifications are harmless — a "fact" misclassified as "incident"
still stores correctly; the type affects retrieval policy, not content.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("brain_v2.decompose")

# ── Classification patterns ──────────────────────────────────────────
# Order matters: first match wins. Patterns are case-insensitive.
_RULE_PATTERNS = re.compile(
    r"(?i)\b("
    r"guardrail|must\s+not|must\s+always|never\s+\w+|always\s+\w+|"
    r"non-negotiable|blocker|constraint|rule:|mandatory|"
    r"do\s+not\s+\w+|prohibited|required|forbidden"
    r")\b"
)
_INCIDENT_PATTERNS = re.compile(
    r"(?i)\b("
    r"bug|broke|broken|crash|regression|incident|failure|"
    r"what\s+went\s+wrong|root\s+cause|post-?mortem|"
    r"what\s+i\s+did\s+wrong|fix(?:ed)?|resolved"
    r")\b"
)
_TASK_PATTERNS = re.compile(
    r"(?i)\b("
    r"todo|to-do|action\s+item|follow[- ]?up|"
    r"need\s+to|should\s+\w+|next\s+step|"
    r"not\s+yet\s+done|outstanding|pending|"
    r"planned|queued|blocked\s+on"
    r")\b"
)


@dataclass
class Chunk:
    text: str
    kind: str
    headline: str
    severity: str | None  # only for rules


def _classify(text: str) -> tuple[str, str | None]:
    """Return (kind, severity). severity is non-None only for rules."""
    if _RULE_PATTERNS.search(text):
        # Distinguish BLOCKER from PATTERN
        if re.search(r"(?i)\b(blocker|non-negotiable|never|must\s+not|prohibited|forbidden)\b", text):
            return "rule", "BLOCKER"
        return "rule", "PATTERN"
    if _INCIDENT_PATTERNS.search(text):
        return "incident", None
    if _TASK_PATTERNS.search(text):
        return "task", None
    return "fact", None


_LEADING_ENUMERATOR = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_HAS_ALPHA = re.compile(r"[^\W\d_]", re.UNICODE)  # any Unicode letter


def _make_headline(text: str, max_words: int = 15) -> str:
    """Extract a headline from text: first sentence, capped at max_words.

    Strips a leading list-enumerator (`1.`, `2)`, `-`, `*`, `•`) FIRST, so a
    numbered list item like "1. Do the retry logic" does not yield the headline
    "1" (which the write gate rejects as non-descriptive — the chunk is then
    dropped, logged, and returned with action="rejected"). If the first-sentence
    slice still has no letter, fall back to the
    first max_words of the whole (de-enumerated) body so the headline is always
    descriptive.
    """
    body = _LEADING_ENUMERATOR.sub("", text.strip())
    first = re.split(r"[.\n;]", body, maxsplit=1)[0].strip()
    if not _HAS_ALPHA.search(first):
        # first "sentence" was numeric/punctuation only — use the body instead.
        first = body.strip()
    words = first.split()
    if len(words) <= max_words:
        return first
    return " ".join(words[:max_words])


def decompose(raw: str) -> list[Chunk]:
    """Split raw context into atomic typed chunks.

    Splitting strategy:
    1. Double-newline = paragraph boundary (primary split)
    2. If a paragraph is still very long (>300 words), split on
       single newlines as secondary boundary
    3. Skip empty chunks and very short ones (<5 words)
    """
    paragraphs = re.split(r"\n\s*\n", raw.strip())
    chunks: list[Chunk] = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Secondary split for very long paragraphs
        if len(para.split()) > 300:
            sub_paras = [s.strip() for s in para.split("\n") if s.strip()]
        else:
            sub_paras = [para]

        for text in sub_paras:
            words = text.split()
            if len(words) < 5:
                log.debug("skipping short chunk (%d words): %s", len(words), text[:50])
                continue

            kind, severity = _classify(text)
            headline = _make_headline(text)
            chunks.append(Chunk(text=text, kind=kind, headline=headline, severity=severity))
            log.debug("classified chunk as %s: %s", kind, headline[:60])

    if not chunks and raw.strip():
        # Fallback: store the whole thing as one fact
        kind, severity = _classify(raw)
        headline = _make_headline(raw)
        chunks.append(Chunk(text=raw.strip(), kind=kind, headline=headline, severity=severity))
        log.info("fallback: stored entire context as single %s", kind)

    from collections import Counter
    log.info("decomposed raw context into %d chunks: %s",
             len(chunks), dict(Counter(c.kind for c in chunks)))
    return chunks
