# RALPH Loop

## Purpose

RALPH is an iterative improvement protocol for multi-session work.

## Acronym

| Step     | Action                                              |
|----------|-----------------------------------------------------|
| **R**eview  | Assess the current state of the codebase         |
| **A**djust  | Make targeted changes based on the review        |
| **L**earn   | Capture what worked and what didn't              |
| **P**lan    | Outline what should happen in the next iteration |
| **H**andoff | Pass complete context to the next session        |

## Protocol

### Review
- Read `CONTEXT_CHECKPOINTS.md`
- Run `validate.sh --all` and note failures
- Check git log for recent changes
- Identify gaps between intended and actual state

### Adjust
- Fix validation failures
- Address technical debt noted in previous sessions
- Refine implementations based on new understanding
- Keep changes small and focused

### Learn
- What patterns are emerging?
- What mistakes keep recurring?
- What tools/approaches are most effective?
- Document lessons in the checkpoint

### Plan
- What are the next 3-5 concrete steps?
- Which specs/constitutions will be needed?
- Are there blockers or dependencies?
- Set realistic scope for next session

### Handoff
- Write a detailed context checkpoint
- Include:
  - Current branch and commit
  - Files changed and why
  - What's working and what's not
  - Clear next steps with file paths
  - Open questions and decisions needed
