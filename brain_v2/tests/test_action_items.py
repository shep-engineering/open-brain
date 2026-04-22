"""Action item compliance gate tests.

Exercises:
  - Create action items and verify they appear in boot payload
  - Write tools blocked when pending action items exist
  - Acknowledge items with all three decision types
  - Writes unblock after all items acknowledged
  - boot_session_v2 reports writes_blocked status
  - Idempotent ack (double-ack same item is fine)
  - Reason required for already_done and not_relevant
  - Invalid decision rejected
"""
from __future__ import annotations

import json

import pytest

from brain_v2 import boot, store


class TestActionItemCreation:
    def test_create_action_item(self, conn):
        # First create a rule to link the action item to
        rule = store.remember_rule(
            conn, headline="Run tests before every push",
            body="pytest must pass locally.", severity="BLOCKER",
            project="test", source="test",
        )
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Verify all tests pass before pushing", project="test",
        )
        assert isinstance(aid, int)
        assert aid > 0

    def test_pending_items_returned(self, conn):
        rule = store.remember_rule(
            conn, headline="Update changelog with releases",
            body="Every release needs a dated entry.", severity="PATTERN",
            project="test", source="test",
        )
        store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Add changelog entry for v0.15.0", project="test",
        )
        pending = store.get_pending_action_items(conn, project="test")
        assert len(pending) == 1
        assert "changelog" in pending[0]["text"].lower()

    def test_count_pending(self, conn):
        rule = store.remember_rule(
            conn, headline="Backup before schema changes",
            body="pg_dump before any migration.", severity="BLOCKER",
            project="test", source="test",
        )
        store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Take backup", project="test",
        )
        store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Verify backup integrity", project="test",
        )
        assert store.count_pending_action_items(conn, project="test") == 2


class TestBootIntegration:
    def test_boot_includes_pending_action_items(self, conn):
        rule = store.remember_rule(
            conn, headline="Never skip the write gate",
            body="All writes go through the 5-step gate.", severity="BLOCKER",
            project="test", source="test",
        )
        store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Confirm write gate is active", project="test",
        )
        payload = boot.build(conn, project="test", task="any", source="test")
        assert len(payload.pending_action_items) == 1
        assert payload.to_dict()["writes_blocked"] is True

    def test_boot_reports_writes_unblocked_when_no_items(self, conn):
        payload = boot.build(conn, project="test", task="any", source="test")
        assert len(payload.pending_action_items) == 0
        assert payload.to_dict()["writes_blocked"] is False


class TestAcknowledgment:
    def _setup_item(self, conn) -> int:
        rule = store.remember_rule(
            conn, headline="Test all new features thoroughly",
            body="No mocks, real DB, real Ollama.", severity="BLOCKER",
            project="test", source="test",
        )
        return store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Write real tests for capture_context", project="test",
        )

    def test_ack_will_execute(self, conn):
        aid = self._setup_item(conn)
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="will_execute", source="test",
        )
        assert result["success"] is True
        assert result["status"] == "will_execute"
        assert store.count_pending_action_items(conn, project="test") == 0

    def test_ack_already_done_requires_reason(self, conn):
        aid = self._setup_item(conn)
        with pytest.raises(ValueError, match="reason"):
            store.acknowledge_action_item(
                conn, item_id=aid, decision="already_done", source="test",
            )

    def test_ack_already_done_with_reason(self, conn):
        aid = self._setup_item(conn)
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="already_done", source="test",
            reason="Completed in prior session, commit abc123.",
        )
        assert result["success"] is True
        assert result["status"] == "already_done"

    def test_ack_not_relevant_requires_reason(self, conn):
        aid = self._setup_item(conn)
        with pytest.raises(ValueError, match="reason"):
            store.acknowledge_action_item(
                conn, item_id=aid, decision="not_relevant", source="test",
            )

    def test_ack_not_relevant_with_reason(self, conn):
        aid = self._setup_item(conn)
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="not_relevant", source="test",
            reason="Current task does not touch this area.",
        )
        assert result["success"] is True
        assert result["status"] == "not_relevant"

    def test_invalid_decision_rejected(self, conn):
        aid = self._setup_item(conn)
        with pytest.raises(ValueError, match="decision"):
            store.acknowledge_action_item(
                conn, item_id=aid, decision="maybe_later", source="test",
            )

    def test_double_ack_idempotent(self, conn):
        aid = self._setup_item(conn)
        store.acknowledge_action_item(
            conn, item_id=aid, decision="will_execute", source="test",
        )
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="will_execute", source="test",
        )
        assert result["success"] is True
        assert result["already_acked"] is True

    def test_nonexistent_item_raises(self, conn):
        with pytest.raises(ValueError, match="not found"):
            store.acknowledge_action_item(
                conn, item_id=99999, decision="will_execute", source="test",
            )


class TestWriteBlocking:
    def _setup_blocking_item(self, conn) -> int:
        rule = store.remember_rule(
            conn, headline="Mandatory pre-flight check requirement",
            body="Every session must complete pre-flight.", severity="BLOCKER",
            project="test", source="test",
        )
        return store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Complete the pre-flight checklist", project="test",
        )

    def test_writes_blocked_when_items_pending(self, conn):
        self._setup_blocking_item(conn)
        assert store.count_pending_action_items(conn, project="test") > 0

    def test_writes_unblocked_after_all_acked(self, conn):
        aid = self._setup_blocking_item(conn)
        store.acknowledge_action_item(
            conn, item_id=aid, decision="will_execute", source="test",
        )
        assert store.count_pending_action_items(conn, project="test") == 0

    def test_multiple_items_all_must_be_acked(self, conn):
        rule = store.remember_rule(
            conn, headline="Multi-step compliance verification",
            body="Several checks required.", severity="BLOCKER",
            project="test", source="test",
        )
        a1 = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Check one", project="test",
        )
        a2 = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="Check two", project="test",
        )
        assert store.count_pending_action_items(conn, project="test") == 2
        store.acknowledge_action_item(
            conn, item_id=a1, decision="will_execute", source="test",
        )
        assert store.count_pending_action_items(conn, project="test") == 1
        store.acknowledge_action_item(
            conn, item_id=a2, decision="will_execute", source="test",
        )
        assert store.count_pending_action_items(conn, project="test") == 0


# ──────────────────────────────────────────────────────────────────────
# brain_v2 2.2.0 — action_item kind field (task vs rule)
# ──────────────────────────────────────────────────────────────────────


class TestActionItemKind:
    """Rule-kind items reject 'already_done' — rules are ongoing and
    don't complete. Closes the memory-capture-avoidance loophole."""

    def _rule(self, conn):
        return store.remember_rule(
            conn, headline="Test rule for kind-field tests",
            body="Placeholder body — exists to link action items.",
            severity="BLOCKER", project="test", source="test",
        )

    def test_create_defaults_kind_to_task(self, conn):
        rule = self._rule(conn)
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="default kind check", project="test",
        )
        pending = store.get_pending_action_items(conn, project="test")
        item = next(p for p in pending if p["id"] == aid)
        assert item["kind"] == "task"

    def test_create_explicit_rule_kind(self, conn):
        rule = self._rule(conn)
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="ongoing discipline", project="test", kind="rule",
        )
        pending = store.get_pending_action_items(conn, project="test")
        item = next(p for p in pending if p["id"] == aid)
        assert item["kind"] == "rule"

    def test_create_rejects_unknown_kind(self, conn):
        rule = self._rule(conn)
        with pytest.raises(ValueError, match="kind"):
            store.create_action_item(
                conn, source_kind="rule", source_id=rule.id,
                text="bad kind", project="test", kind="maybe_rule",
            )

    def test_ack_rule_rejects_already_done(self, conn):
        rule = self._rule(conn)
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="rule — must always do X", project="test", kind="rule",
        )
        with pytest.raises(ValueError, match="don't complete"):
            store.acknowledge_action_item(
                conn, item_id=aid, decision="already_done",
                source="test", reason="tried to bypass",
            )
        # Still pending — rejection didn't flip the row.
        assert store.count_pending_action_items(conn, project="test") == 1

    def test_ack_rule_accepts_will_execute(self, conn):
        rule = self._rule(conn)
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="rule to follow", project="test", kind="rule",
        )
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="will_execute", source="test",
        )
        assert result["success"] is True
        assert result["kind"] == "rule"

    def test_ack_rule_accepts_not_relevant_with_reason(self, conn):
        rule = self._rule(conn)
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="rule to bypass", project="test", kind="rule",
        )
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="not_relevant",
            source="test", reason="task is read-only, rule does not apply",
        )
        assert result["success"] is True
        assert result["kind"] == "rule"

    def test_ack_task_kind_still_supports_already_done(self, conn):
        """Task-kind (default) preserves existing ack semantics."""
        rule = self._rule(conn)
        aid = store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="one-shot task", project="test", kind="task",
        )
        result = store.acknowledge_action_item(
            conn, item_id=aid, decision="already_done",
            source="test", reason="done in prior session",
        )
        assert result["success"] is True
        assert result["kind"] == "task"
        assert result["status"] == "already_done"

    def test_pending_list_includes_kind(self, conn):
        rule = self._rule(conn)
        store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="task item", project="test", kind="task",
        )
        store.create_action_item(
            conn, source_kind="rule", source_id=rule.id,
            text="rule item", project="test", kind="rule",
        )
        pending = store.get_pending_action_items(conn, project="test")
        kinds = {p["text"]: p["kind"] for p in pending}
        assert kinds["task item"] == "task"
        assert kinds["rule item"] == "rule"
