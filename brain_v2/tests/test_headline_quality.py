"""Headline quality gate: reject non-descriptive headlines at write time.

The live corpus accumulated 55 junk memories headlined "1","2","3"... because
check_headline only enforced non-empty + <=15 words. These embed poorly and
pollute retrieval. The fix rejects a headline with NO ascii letter, while
accepting real short headlines (NIST, Emergent, RLS Pattern A). The decompose
fix ensures a numbered-list item is not turned into a bare-number headline that
would then be dropped.

check_headline is a pure function (no DB) — these tests do not need a DB.
The decompose test is also pure.
"""
from __future__ import annotations

import pytest

from brain_v2.write_gate import check_headline, WriteGateError
from brain_v2.decompose import _make_headline


# ── reject: non-descriptive (no letter) ──────────────────────────────────
@pytest.mark.parametrize("bad", ["1", "42", "3.", "—", "  7  ", "999", "()", "2)"])
def test_rejects_non_descriptive_headline(bad):
    with pytest.raises(WriteGateError, match="non-descriptive"):
        check_headline(bad)


def test_rejects_empty():
    with pytest.raises(WriteGateError, match="required"):
        check_headline("   ")


# ── accept: real short headlines that exist in the live corpus ────────────
@pytest.mark.parametrize("good", [
    "NIST", "Config", "CLAUDE", "Emergent", "auto_rebuild", "AI_REMEDIATION_PLAN",
    "RLS Pattern A", "ARC-822 done", "Use pooler", "DELIVERED:", "Architecture:",
    "3 auth lanes",  # starts with a digit but HAS letters -> descriptive, keep
])
def test_accepts_real_short_headlines(good):
    check_headline(good)  # must not raise


@pytest.mark.parametrize("good", ["中文标题", "café", "Résumé notes", "naïve approach"])
def test_accepts_non_latin_and_accented_headlines(good):
    check_headline(good)  # any Unicode letter is descriptive; must not raise


def test_word_cap_still_enforced():
    with pytest.raises(WriteGateError, match=">15 words"):
        check_headline(" ".join(["word"] * 16))


# ── decompose: numbered list item must NOT become a bare-number headline ──
def test_numbered_list_item_yields_descriptive_headline():
    h = _make_headline("1. Do the retry logic for failed webhook deliveries now")
    assert h != "1"
    # and it passes the gate (would previously be dropped)
    check_headline(h)
    assert "retry" in h.lower()


@pytest.mark.parametrize("prefix", ["1. ", "2) ", "- ", "* ", "• "])
def test_various_enumerators_stripped(prefix):
    h = _make_headline(prefix + "Configure the pooler for transaction mode")
    check_headline(h)  # must not raise
    assert h.lower().startswith("configure")


def test_pure_number_body_falls_back_without_crashing():
    # degenerate: body is just a number. Headline has no letter -> the gate will
    # reject it, but _make_headline must not crash producing it.
    h = _make_headline("1.")
    assert isinstance(h, str)
