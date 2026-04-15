# Source Consultation Map

## Purpose

This map defines authoritative sources for different topics,
preventing hallucination and ensuring accurate information.

## How to Use

Before answering a domain-specific question or making a decision:

1. Check this map for the relevant topic
2. Consult the listed source
3. Cite the source in your response

## Map

| Topic                  | Authoritative Source                              |
|-----------------------|---------------------------------------------------|
| Project configuration | `archetype-orchestrator.yml` in project root      |
| Spec/archetype rules  | The spec's `*-constitution.md` file               |
| Validation rules      | `archetype-orchestrator/scripts/validate.sh`      |
| Discovered specs      | `python archetype-orchestrator/engine/discover.py --scan` |
| Task state            | `.archetypes/task-state/task-start.env`            |
| Validation config     | `.archetypes/validate.env`                        |
| Git branch rules      | `archetype-orchestrator/hooks/pre-commit`         |
| CI/CD config          | `.github/workflows/` (generated from templates)   |
| Context history       | `docs/planning/CONTEXT_CHECKPOINTS.md`            |
| Semver version        | `package.json` or equivalent version file         |
| Security rules        | Security-guardian constitution (if discovered)     |
| API contracts         | Integration-specialist constitution (if discovered)|
| Data pipeline rules   | Data-pipeline-builder constitution (if discovered) |

## Rules

1. Never fabricate information about project structure
2. Always run discovery before assuming what specs exist
3. Cite file paths when referencing project-specific information
4. If a source is unavailable, say so — don't guess
