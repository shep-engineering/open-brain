# Testing

Open Brain uses an isolated test database to ensure tests never touch production data.

---

## Architecture

```
PRODUCTION                          TESTING
============                        ============
docker-compose.yml                  docker-compose.test.yml
  container: open-brain-db            container: open-brain-test-db
  port: 5432                          port: 5434
  database: openbrain                 database: openbrain_test
  volume: open_brain_data             volume: none (ephemeral)
```

Two completely separate PostgreSQL containers. Different ports, different databases, different Docker containers. The test container has no persistent volume — data is wiped when the container is removed.

---

## Quick Start

```sh
# Start the test database
docker compose -f docker-compose.test.yml up -d

# Run all tests
pytest tests/ -v

# Or use the convenience script
bash scripts/test-db.sh -v
```

---

## How It Works

### Three Layers of Production Safety

1. **Environment override** — `conftest.py` sets `DATABASE_URL` to the test database at module-load time, before `server.py` is imported by any test.

2. **Session assertion** — A session-scoped fixture reads `server.DATABASE_URL` after import and calls `pytest.exit()` if it doesn't contain `openbrain_test` on port `5434`.

3. **Connection reset** — The singleton `server._conn` is set to `None` before tests run, forcing `_get_conn()` to create a fresh connection to the test database.

### Schema Initialization

The test `conftest.py` automatically creates the full schema in the test database on first run:

- pgvector extension
- `memories` table with all columns and indexes
- `memories_audit` table with audit trigger
- Hybrid search (FTS), bi-temporal, and uptime migrations

This mirrors the production schema exactly. You never need to run `setup_db.py` against the test database manually.

### Fake Embeddings

By default, tests use **deterministic fake embeddings** instead of calling Ollama:

- SHA-256 hash of input text, expanded to 768 dimensions, L2-normalized
- Different inputs produce different vectors (cosine similarity varies)
- No Ollama or OpenAI dependency needed
- Tests run fast and are CI-safe

To run tests with **real Ollama embeddings**, use the `ollama` marker:

```sh
pytest tests/ -m ollama -v
```

---

## Test Files

| File | What it tests | Needs DB? |
|------|---------------|-----------|
| `tests/test_pinned_memories.py` | Pinned/guardrail memory CRUD and protection | Yes |
| `tests/test_session_compliance.py` | Polling-based compliance enforcement | Yes |
| `tests/test_secrets_filter.py` | Secret detection patterns and false positives | No |
| `tests/test_infrastructure.py` | Pure-Python launcher (`bring_up`/`bring_down`, graceful Ctrl+Break, ownership model). Mocked subprocess + urllib. | No |
| `tests/mobile/test_demo_nav.py` | Reveal.js demo deck on mobile — Pixel 5 emulation, portrait + landscape, chevron taps + swipes + rotation. | No (uses Playwright's bundled Chromium) |

All test files that touch the database use project-scoped cleanup fixtures (`__test_pinned__`, `__test_compliance__`) for additional isolation.

---

## Mobile UX evaluator harness

`tests/mobile/test_demo_nav.py` is a self-contained Playwright harness
that builds the docs site (`mkdocs build`), serves it on a local port,
opens it in Chromium with Pixel 5 device emulation, and drives the
reveal.js demo deck through real touch + pointer events. Catches mobile
UX regressions that desktop browser checks miss entirely.

### Run

```sh
.venv\Scripts\python.exe tests/mobile/test_demo_nav.py
```

Exit code is 0 iff all 20 assertions pass. Failures drop screenshots in
`logs/mobile-test-shots/` for visual evidence.

### What it asserts

For both `portrait (393x851)` and `landscape (851x393)`:

1. Page loads on slide 0
2. Right + left chevrons render and are visible
3. Tap right chevron → slide advances; tap left → goes back
4. Horizontal swipe R→L advances; L→R goes back
5. **Vertical swipe (swipe-up) does NOT navigate** — locks navigation to horizontal gestures + chevrons

Plus a portrait→landscape rotation assertion that the mobile chrome
(chevrons + slide counter) survives the viewport change without `at-edge`
disabling pointer events.

### Harness-fidelity disclaimers

Documented in the test file. Briefly: Playwright's `set_viewport_size`
doesn't fire the full layout-recompute chain Reveal needs after rotation,
so post-rotation `Reveal.next()` silently no-ops in this harness even
though it works on a real phone (where the OS-level rotation fires
`orientationchange` properly). The rotation test therefore asserts
**chrome survival** but not **post-rotation navigation** — that's
deferred to the planned Tier-2 AVD/adb harness.

### When to add this to CI

Currently runs locally before any `docs/demo/index.html` change is
deployed. Add to CI when an `mkdocs gh-deploy` workflow is wired up —
the harness should gate the deploy on green.

---

## Cleanup

After the test session, `conftest.py` truncates all tables in the test database. To fully remove the test container:

```sh
docker compose -f docker-compose.test.yml down
```

Since there's no volume, all data is gone.

---

## Troubleshooting

### Tests skip with "Test database not running"

Start the container:

```sh
docker compose -f docker-compose.test.yml up -d
```

### Port 5434 is in use

Check what's using it:

```sh
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep 5434
```

### Tests accidentally hit production

This should be impossible with the three safety layers. If you see it, check that `conftest.py` is at the project root (not inside `tests/`). pytest must discover it before any test module imports `server`.
