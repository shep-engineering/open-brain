# Planning

This repo is public. Architectural planning docs, Windsurf-reviewed
designs, and session-decision history are kept in a separate **private**
sibling repo to keep internal deliberation out of the public view.

## If you have access

Planning content lives at `../open-brain-planning/` on disk
(GitHub: `degailen/open-brain-planning`, private).

First-time clone:

```bash
git clone git@github-degailen:degailen/open-brain-planning.git ../open-brain-planning
```

Search plans:

```bash
bash scripts/plan-grep.sh "<keyword>"
# Windows PowerShell:
powershell scripts/plan-grep.ps1 -Pattern "<keyword>"
```

## If you don't (public contributor)

The public design surface is:

- [CHANGELOG.md](./CHANGELOG.md) — release history with per-version design rationale.
- Code comments in `server.py`, `brain_v2/`, `scripts/`.
- [docs/references.md](./docs/references.md) — external reading and architectural background.
- Issues and PRs on this repo — that's where feature discussion happens publicly.

This split is intentional: rough drafts, session-by-session design
iteration, and internal working notes live in the private repo; the
reasoning that matters for users of this project lives in the sources
above.
