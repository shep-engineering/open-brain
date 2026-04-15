# Dual-Agent Workflow

## Purpose

Complex tasks benefit from separating planning from execution.
This protocol defines two roles that work in tandem.

## Roles

### Planner

- Reads the task requirements
- Discovers relevant specs via `discover.py --query`
- Reads the matched constitution
- Creates a numbered, step-by-step implementation plan
- Defines success criteria for each step
- Does NOT write code

### Builder

- Receives the plan from the Planner
- Executes each step in order
- Reports status after each step
- Asks the Planner for clarification when blocked
- Runs validation after completing the plan

## Protocol

```
Planner                          Builder
   │                                │
   ├── 1. Analyze task              │
   ├── 2. Discover spec             │
   ├── 3. Read constitution         │
   ├── 4. Create plan ─────────────►│
   │                                ├── 5. Execute step 1
   │                                ├── 6. Report status
   │◄── 7. Review / adjust ────────┤
   │                                ├── 8. Execute step 2
   │                                ├── ...
   │                                ├── N. Run validation
   ├── N+1. Final review ──────────►│
   │                                ├── N+2. Fix issues
   │                                └── N+3. Commit
```

## Rules

1. Planner always writes the plan before Builder starts
2. Builder follows the plan exactly unless blocked
3. If Builder deviates, Planner must approve the change
4. Validation must pass before the task is considered done
5. Both roles create context checkpoints as needed
