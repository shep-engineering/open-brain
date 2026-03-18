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
