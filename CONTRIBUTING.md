# Contributing to Open Brain

Open Brain is a persistent AI memory server (MCP + pgvector + local
LLMs). Contributions are welcome — bug reports, fixes, new tool
implementations, docs improvements, or design suggestions.

## Quick start

1. **Fork** `shep-engineering/open-brain` on GitHub.
2. **Clone your fork** locally and branch from `main`:
   ```bash
   git clone git@github.com:<your-user>/open-brain.git
   cd open-brain
   git checkout -b feat/your-change
   ```
3. **Set up the dev environment.** The project runs on Python 3.11+,
   Postgres 16 with pgvector, and a local Ollama install. See `README.md`
   for the full boot sequence.
4. **Make your change.** Keep commits focused; one logical change per PR.
5. **Run the tests:**
   ```bash
   pytest tests/           # v1 suite (parallel: pytest tests/ -n 4 --dist loadfile)
   pytest brain_v2/tests/  # v2 suite (serial)
   ```
6. **Open a PR** against `main`. Fill in the PR template — a short
   description, test command, and a link to any related issue is plenty.

## What to expect on merge

- PRs are reviewed against `shep-engineering/open-brain` (the repo
  you forked from).
- Responses in days-to-a-week range. This is a solo-maintained project.
- Small focused PRs merge faster than large refactors — if you're
  about to change more than ~5 files, consider opening an issue first
  to sanity-check the approach.
- All merged contributions are preserved as normal git commits with
  your author attribution intact.

## Scope

Good fits for a PR:
- Bug fixes (test-reproducible preferred)
- New MCP tool implementations following patterns in `server.py` / `brain_v2/server.py`
- Documentation improvements (`README.md`, `docs/*.md`, code comments)
- Test coverage for existing behavior
- Small quality-of-life improvements to scripts in `scripts/`

Things to open an issue for first:
- Schema changes (v1: via `scripts/migrate_vN_*.py`; v2: via
  `brain_v2/schema.py`)
- Architectural changes to the session registry, write-gate, or
  supersede semantics
- Anything that touches the observability/heartbeat infrastructure

## Code style

- Python: PEP 8, 4-space indent. `__future__` imports at the top of
  new files.
- Type hints on public functions where they aid readability.
- Tests in `tests/` (v1) or `brain_v2/tests/` (v2), using pytest.
- No new dependencies without discussion — v1 and v2 are kept small
  intentionally.

## Commit messages

Conventional commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, etc.)
are appreciated but not required.

## License

By submitting a PR you agree your contribution is licensed under the
[MIT License](./LICENSE) to match the project.
