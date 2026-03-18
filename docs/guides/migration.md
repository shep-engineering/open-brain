# Migration

## Upgrading from v1 to v2

v2 adds project scoping, annotations, access tracking, quality signals, and deduplication. Run the migration script:

```sh
python scripts/migrate_v2.py
```

This adds new columns with sensible defaults. Safe to re-run. Existing memories are unaffected.

### What's New in v2

| Feature | v1 | v2 |
|---------|----|----|
| Project scoping | No | Tag and filter by project |
| Incremental retrieval | Full content always | 200-char previews, then `recall` for full |
| Annotations | No | Attach notes to existing memories |
| Quality signals | No | Rate memories up/down |
| Access tracking | No | Count recalls, track last accessed |
| Deduplication | No | Skip near-identical memories (cosine >= 0.92) |
| Batch capture | No | LLM decomposition + phased batching |

---

## Migrating from Another Memory System

If you're moving from a different AI memory tool (Claude's built-in memory, ChatGPT memory, etc.), run this prompt in your current AI:

```
Export everything you know about me, including my projects, preferences, key people,
past decisions, ongoing work, and constraints, as a series of plain-text
notes, one per line. I'm migrating to a new memory system.
```

Then feed each line to `remember` to seed your Open Brain.
