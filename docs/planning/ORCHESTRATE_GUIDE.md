# Orchestration Guide

## Quick Reference

### Initialize a New Project

```bash
bash archetype-orchestrator/scripts/init.sh
```

### Start a Task

```bash
git checkout -b feat/my-task
bash archetype-orchestrator/scripts/pre-work-check.sh
```

### During Work

```bash
# Discover available specs
python archetype-orchestrator/engine/discover.py --scan

# Route a task to the right spec
python archetype-orchestrator/engine/discover.py --query "keywords"

# Save a context checkpoint
bash archetype-orchestrator/scripts/context-checkpoint.sh "milestone description"
```

### End a Task

```bash
bash archetype-orchestrator/scripts/post-work-check.sh
git add -A && git commit -m "feat: description"
```

### Generate Workflows

```bash
bash archetype-orchestrator/engine/generate-workflows.sh
```

### Install CI Templates

```bash
bash archetype-orchestrator/scripts/install-ci.sh
```

### Validate Everything

```bash
bash archetype-orchestrator/scripts/validate.sh --all
```

### Clean Up Merged Branches

```bash
bash archetype-orchestrator/scripts/cleanup-branches.sh
```
