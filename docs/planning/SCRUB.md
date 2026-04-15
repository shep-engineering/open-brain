# Open Brain — Shep-Engineering Orphan Push: Credential/PII/Path Audit

**Scope:** pre-flight audit for the parked `shep/main` orphan push (see brain task #834).
**Method:** systematic grep across the tracked repo tree (excluding `.venv/`, `site/`, `.git/`) for
secret patterns, hardcoded personal paths, real identifiers, cross-project path leakage, and
tracked runtime artifacts.
**Date:** 2026-04-13
**Status:** audit complete, scrub actions listed below. Do NOT proceed with `shep/main` orphan
push until the Category A + B items are resolved (decide per-item: scrub in-place, gitignore +
remove, or accept).

---

## Severity legend

| Level | Meaning |
|---|---|
| **BLOCKER** | Must fix before orphan push — actively leaks sensitive info or breaks for anyone else |
| **HIGH** | Should fix before orphan push — leaks dev environment / private infra |
| **MEDIUM** | Recommended fix — embarrassing or amateur for a marketable product |
| **LOW** | Nice to fix — informational or minor concern |
| **ACCEPTED** | Reviewed and fine as-is (still listed for transparency) |

---

## Category A — Hardcoded `F:\open-brain` paths (HIGH)

**Why it matters:** reveals Shep's local dev directory structure to every public visitor.
Also breaks portability: nothing will work for anyone else without find-and-replace.

**Locations & counts:**

| File | Instances | Context | Recommended action |
|---|---|---|---|
| `start.ps1:15` | 1 | `$OB = "F:\open-brain"` | Replace with `$PSScriptRoot` or `Split-Path -Parent $MyInvocation.MyCommand.Path` |
| `stop.ps1:3` | 1 | `$OB = "F:\open-brain"` | Same as start.ps1 |
| `scripts/windows/open-brain-on.cmd:4,27,50,55` | 4 | Hard paths to venv/python, logs, server | Use `%~dp0..\..\` relative base, set as `OB_ROOT` at top |
| `scripts/windows/open-brain-dashboard.cmd:9` | 1 | `F:\open-brain\.venv\Scripts\pythonw.exe F:\open-brain\dashboard.py` | Use `%~dp0..\..\` relative base |
| `scripts/windows/backup-brain.cmd:5` | 1 | `set BACKUP_DIR=F:\open-brain\backups` | Relative base |
| `scripts/windows/open-brain-sse-proxy.cmd` | ? | (not inspected — likely similar) | Audit and relative-ize |
| `scripts/windows/create-desktop-shortcuts.ps1:5,6,12,13,19,20,26,27,28` | 8 | Shortcut target paths | Compute from `$PSScriptRoot` at script start |
| `scripts/ensure-stack.sh:9,10` | 2 | Comment-only usage examples | Replace with `/path/to/open-brain/...` placeholder |
| `scripts/make_icon.py:2,12,16` | 3 | `F:\comfyui\...` input + `F:\open-brain\assets\...` output | **See Category B** — this script references an EXTERNAL directory (comfyui) |
| `server.py:16,17` | 2 | Docstring example MCP client config | Change example to `<OPEN_BRAIN_ROOT>\...` placeholder |
| `dashboard.py:4` | 1 | Docstring usage example | Change to `<OPEN_BRAIN_ROOT>\...` placeholder |
| `AGENTS.md:20,21,27,28` | 4 | User-facing OS startup table | Keep SOME — they're user instructions — but mark as "example on Windows; adjust to your install path" |
| `.task-markers/20260325-094611-done.txt` | 1 | "F:/open-brain/CLAUDE.md script paths fixed" — internal note | See Category E (.task-markers shouldn't be tracked) |

**Fix pattern for scripts (preferred):**
```powershell
# start.ps1 / stop.ps1
$OB = Split-Path -Parent $MyInvocation.MyCommand.Path
```
```cmd
REM *.cmd
set OB_ROOT=%~dp0..\..
REM now use %OB_ROOT%\.venv\Scripts\python.exe etc.
```

---

## Category B — Cross-project path leakage (HIGH)

**Why it matters:** reveals *other* private projects on Shep's machine — not even the open-brain
repo. A stranger reading the source would learn about `F:\my-archetypes`, `F:\comfyui`, `F:\AI`.
That's accidentally broadcasting the shape of the personal dev environment.

| File | Leak | Severity |
|---|---|---|
| `telemetry.py:3` | Docstring: "Follows the Observability Archetype constitution in `F:\my-archetypes\observability`" | HIGH — reveals sibling project |
| `scripts/make_icon.py:2` | Input: `F:\comfyui\output\open-brain-concept-art.png` | HIGH — reveals unrelated tool install |
| `.windsurf/workflows/scaffold-AI.md:10` | Reference: `F:\AI\AI Workstation Setup.md` | MEDIUM — reveals `F:\AI` directory exists |

**Fix:**
- `telemetry.py` — remove the specific path; say "Follows the Observability Archetype constitution (see archetype-orchestrator project)."
- `scripts/make_icon.py` — either (a) delete the script (it's a one-shot icon generator), (b) take the input path as `sys.argv[1]`, or (c) move the source PNG into `assets/` inside the repo.
- `.windsurf/workflows/scaffold-AI.md` — replace with a generic reference like "your local AI workstation setup document."

---

## Category C — Database password pattern `postgres:password` (LOW / ACCEPTED)

**Where:**
- `dashboard.py:24`, `scripts/infrastructure.py:110`, `scripts/migrate_v2.py:23`,
  `scripts/migrate_v3_pinned.py:21`, `scripts/setup_db.py:18`, `scripts/windows/open-brain-on.cmd:27`,
  `server.py:48`, `docker-compose.yml:9`, `conftest.py:23` (test, uses `testpassword` on port 5434)

**Analysis:** every instance is either (a) in `os.getenv("DATABASE_URL", "postgresql://postgres:password@...")` as the *fallback default* for local dev, (b) in `docker-compose.yml` as `POSTGRES_PASSWORD: password` — the default dev password, or (c) in test config with a distinct test password. No production or real deployment references. **Acceptable.** Users who actually deploy open-brain would override via `DATABASE_URL` env var.

**Minor polish (optional):**
- Add a prominent warning at the top of `docker-compose.yml` and in `README.md` installation:
  "The default postgres password is `password` for dev convenience only. Override via the
  `POSTGRES_PASSWORD` env var (or `DATABASE_URL`) for any real deployment."
- Or, require `POSTGRES_PASSWORD` env var in docker-compose.yml with no default.

---

## Category D — Real credentials / secrets / tokens (BLOCKER — result: NONE FOUND ✅)

**Patterns searched:**
- API keys: `ghp_...`, `github_pat_...`, `sk-...`, `sk-ant-...`, `xoxb-...`, `AIza...`, `SG.`
- Generic tokens / JWTs / private keys / bearer tokens
- Hardcoded passwords beyond the `postgres:password` dev default

**Findings:** the only `sk-ant-` pattern in the tree is `tests/test_secrets_filter.py:123` — a
fabricated test fixture used to verify the secret-filter rejects it. That's intentional and
acceptable. **No real secrets leaked.** The secrets filter + development discipline held up.

---

## Category E — Tracked runtime artifacts (MEDIUM)

| Path | Tracked? | Problem | Action |
|---|---|---|---|
| `.task-markers/*.txt` | 6 files tracked (March 2026); 6 files untracked (April) | WIP timestamps + brief test notes. Noise for public repo. At least one leaks a path (F:/open-brain/CLAUDE.md). | Add `.task-markers/` to `.gitignore`, `git rm --cached .task-markers/*.txt` |
| `backups/*.sql` | 0 tracked (gitignored) | — | ✅ fine |
| `logs/*` | 0 tracked (gitignored) | — | ✅ fine |
| `docs/planning/*` | 10 tracked | Proprietary. Filtered from public build via `exclude_docs`, but still IN the repo tree. | **For orphan push ONLY:** filter these from the orphan tree. Do NOT delete from degailen/main — they're legit internal docs. |
| `.open-brain.pid` | 0 tracked (gitignored) | — | ✅ fine |
| `logs/ollama.pid` | 0 tracked (gitignored) | — | ✅ fine (created at runtime by new v0.9.0 infrastructure.py) |

---

## Category F — External / third-party content (LOW / CHECK)

| File | Concern |
|---|---|
| `Agent Memory_ Why Your AI Has Amnesia and How to Fix It _ developers.pdf` (5.8 MB) | A third-party PDF. Verify provenance/licensing — if it's a publicly redistributable article it's fine; if it's an internal Anthropic/Google/Microsoft-issued doc, may not be licensed for re-upload. **Action:** confirm the PDF's source + license before the orphan push. Worst case, replace with a link. |
| `docs/assets/videos/brain-video.mp4` | Hero video on index.md. Presumably Shep-created or licensed. Confirm. |
| `docs/assets/` images | Shep-created / CC0 expected. Confirm. |

---

## Category G — Git identity for the orphan commit (BLOCKER)

Current local `git config`:
```
user.name  = degailen
user.email = degailen@gmail.com
```

The orphan commit's Author field will be whoever is configured when `git commit` runs. If this
is pushed to `shep-engineering/open-brain` as-is, the public commit will be authored by
`degailen <degailen@gmail.com>`. For the public release under the shep-engineering identity,
switch the committer:

```sh
# Option 1: per-commit (recommended for this one push)
GIT_AUTHOR_NAME="Shep Engineering" GIT_AUTHOR_EMAIL="<shep-engineering-email>" \
GIT_COMMITTER_NAME="Shep Engineering" GIT_COMMITTER_EMAIL="<shep-engineering-email>" \
git commit ...

# Option 2: local config in a throwaway clone
cd /tmp/open-brain-orphan
git config user.name "Shep Engineering"
git config user.email "<shep-engineering-email>"
git commit ...
```

**Action:** decide the public committer identity (name + email) BEFORE the orphan push.
Anthropic's `noreply` co-author trailer is unrelated to this — that's for claude attribution,
not primary authorship.

---

## Category H — GitHub Actions / CI workflows (NONE)

`.github/workflows/` does not exist in the repo. Only `.github/copilot-instructions.md` and
`.github/prompts/scaffold-AI.prompt.md`. Both contain references that need Category A scrub
review but no CI token secrets.

---

## Category I — IDE and agent config files (LOW)

Tracked:
- `.cursorrules`, `.clinerules`, `.windsurf/workflows/*.md`, `.github/copilot-instructions.md`,
  `AGENTS.md`, `CLAUDE.md`, `prompts/*.md`

**Findings:**
- `AGENTS.md` contains the `F:\open-brain\...` paths in the user-facing OS startup table
  (Category A).
- `.windsurf/workflows/scaffold-AI.md` references `F:\AI\...` (Category B).
- None of the agent configs contain real credentials, API keys, or PII. They're all instructions
  to agents, written in neutral language.

---

## Suggested orphan-push procedure (for the future review session)

1. **Execute Category A, B, E, G fixes** on a dedicated branch (`chore/pre-public-scrub`) of
   `degailen/main`. Commit normally — these are genuinely portable improvements, not just
   pre-flight cleanup.
2. **Decide PDF provenance** (Category F) — keep, replace, or remove.
3. **Create the orphan tree in a throwaway directory** (leave `degailen/main` untouched during
   the orphan build):
   ```sh
   mkdir /tmp/open-brain-orphan && cd /tmp/open-brain-orphan
   git clone --depth=1 -b main git@github-degailen:degailen/open-brain.git .
   rm -rf .git .task-markers docs/planning "Agent Memory_ ... .pdf"  # if deciding to drop the PDF
   git init
   git checkout --orphan main
   git config user.name "Shep Engineering"
   git config user.email "<shep-engineering-email>"
   git add -A
   git commit -m "Open Brain v0.9.0 — graceful shutdown + ownership model"
   git remote add shep git@github-shep:shep-engineering/open-brain.git
   git push --force shep main:main
   ```
4. **Verify post-push:** `git ls-remote shep` should show a single commit on `main`; browse
   the repo on github.com/shep-engineering/open-brain to spot-check that nothing obvious
   leaked (particularly the planning/ dir absence).

---

## Summary

| Category | Status | Blocker? |
|---|---|---|
| A — Hardcoded F:\open-brain paths | ~18 instances across ~12 files | HIGH, not blocker but should fix |
| B — Cross-project path leakage (comfyui, my-archetypes, F:\AI) | 3 instances | HIGH |
| C — `postgres:password` dev default | ~9 instances | ACCEPTED (dev default pattern) |
| D — Real secrets / API keys | 0 found | ✅ |
| E — Tracked runtime artifacts (.task-markers, etc.) | 6 tracked .task-markers | MEDIUM |
| F — Third-party PDF / video | 2 assets | CHECK provenance |
| G — Git commit identity | degailen → needs shep-engineering | BLOCKER |
| H — GH Actions | None | ✅ |
| I — IDE/agent configs | Only Category A/B leakage | — |

**Recommended order:** G (identity decision), A (path scrub), B (cross-project leak), E (task-marker cleanup), F (PDF provenance) — then orphan push.
