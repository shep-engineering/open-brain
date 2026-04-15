---
name: Archetype Check
description: >
  Run archetype-orchestrator validation against the current project.
  Discovers specs and validates code against all archetype-orchestrator rules.
---

# Step 1 — Sync Configuration

```bash
bash archetype-orchestrator/scripts/sync-validate-env.sh
```

# Step 2 — Discover Specs

```bash
python archetype-orchestrator/engine/discover.py --scan
```

Review the discovered specs. Note any that are missing or unexpected.

# Step 3 — Run Full Validation

```bash
bash archetype-orchestrator/scripts/validate.sh --all
```

# Step 4 — Report Results

If validation passed:
- Report success
- Note any warnings

If validation failed:
- List each error with its file and line
- Suggest fixes for each error
- Re-run validation after fixing
