---
name: Orchestrate
description: >
  Discovery-based orchestration workflow. Discovers available specs,
  routes the task to the best match, loads its constitution, and
  enforces pre/post-work gates.
---

# Step 1 — Pre-Work Gate

Run the pre-work check to ensure branch hygiene and create a task marker:

```bash
bash archetype-orchestrator/scripts/pre-work-check.sh
```

If this fails, stop and fix the issue (usually: create a feature branch).

# Step 2 — Discover Available Specs

Scan for all available archetypes, personas, and specs:

```bash
python archetype-orchestrator/engine/discover.py --scan
```

Review the output. Note how many specs were found and what domains they cover.

# Step 3 — Route the Task

Describe the user's task and find the best-matching spec:

```bash
python archetype-orchestrator/engine/discover.py --query "<USER_TASK_DESCRIPTION>"
```

If the match confidence is high (>= 0.3):
- Read the matched spec's constitution file
- Follow its rules for the remainder of this task

If no match or low confidence:
- Apply universal governance rules only
- Inform the user that no specialist spec was found

# Step 4 — Read the Constitution

Open and read the constitution file returned by the query:

```
<CONSTITUTION_PATH from Step 3>
```

This is now your directive. Follow it precisely.

# Step 5 — Execute the Task

Apply both:
1. The spec's domain-specific rules (from the constitution)
2. Universal governance rules (security, docs, conventional commits)

Work incrementally. At each meaningful milestone, create a context checkpoint:

```bash
bash archetype-orchestrator/scripts/context-checkpoint.sh "description of milestone"
```

# Step 6 — Post-Work Gate

When the task is complete, run the post-work check:

```bash
bash archetype-orchestrator/scripts/post-work-check.sh
```

This validates all archetype-orchestrator rules and creates a task-end marker.
If validation fails, fix the issues and re-run.

# Step 7 — Summary

Report to the user:
- What spec was used (or "universal rules" if none matched)
- What was accomplished
- Validation status
- Any warnings or recommendations
