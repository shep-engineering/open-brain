---
name: Pre-Work Checklist
description: >
  Run the pre-work checklist before starting any task.
  Ensures branch hygiene, context loading, and spec discovery.
---

# Step 1 — Branch Check

Ensure you are on a feature branch (not main/master/develop):

```bash
git rev-parse --abbrev-ref HEAD
```

If on a protected branch, create a feature branch:

```bash
git checkout -b feat/<descriptive-name>
```

# Step 2 — Load Context

Read the context checkpoints to understand prior work:

```bash
cat docs/planning/CONTEXT_CHECKPOINTS.md
```

# Step 2b — Read Live Configuration

**Always read `.env` (or `.env.example` if `.env` doesn't exist) before planning any work.**
Never assume features are unavailable based on defaults. Check what is actually configured:

- Is `METADATA_LLM_MODEL` set? If yes, LLM-dependent features (smart merge, consolidation, richer metadata) are available.
- Which Ollama models are available? Check: `curl -s http://localhost:11434/api/tags`
- Is the DB reachable? Check `DATABASE_URL`.

Do not defer any feature as "LLM-dependent" until you have confirmed `METADATA_LLM_MODEL` is actually blank in the live `.env`.

# Step 3 — Pre-Work Gate

Run the pre-work check:

```bash
bash archetype-orchestrator/scripts/pre-work-check.sh
```

# Step 4 — Discover Specs

```bash
python archetype-orchestrator/engine/discover.py --scan
```

# Step 5 — Ready

Report:
- Current branch name
- Number of discovered specs
- Summary of last context checkpoint
- Ready to begin work
