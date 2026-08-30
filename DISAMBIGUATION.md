# Disambiguation — Which "Open Brain"?

"Open Brain" is a surprisingly popular project name in 2026. If you
landed here expecting something else, this page points you at the
right place.

**This repository** is:

- **Name:** Open Brain (sometimes `open-brain` as the repo slug)
- **Maintainer:** David Sheppard (GitHub: `shep-engineering` publicly, `degailen` on the source side)
- **URL:** <https://github.com/shep-engineering/open-brain>
- **Website:** <https://shep-engineering.github.io/open-brain/>
- **What it does:** Personal AI memory server — Postgres + pgvector + local Ollama + Model Context Protocol (MCP) so every AI tool you use (Claude Code, Cursor, Windsurf, ChatGPT Desktop, VS Code Copilot) shares the same persistent memory of you.
- **License:** MIT

If that's what you were looking for — welcome. Everything else on this page is about telling us apart from similarly-named projects we are NOT affiliated with.

---

## Other projects named "Open Brain" or variants (AI-memory space)

These all sit in roughly the same problem space as us (persistent memory for AI tools), making confusion especially likely. **We are not affiliated with any of them.** Naming collisions in the "memory for AI" space are common because both words — "open" (as in open source) and "brain" (as in memory/cognition) — are obvious fits.

### OB1 — Open Brain (Nate B. Jones)

- **GitHub:** <https://github.com/NateBJones-Projects/OB1>
- **Launched:** March 2026
- **Tagline:** "The infrastructure layer for your thinking. One database, one AI gateway, one chat channel — any AI plugs in."
- **Approach:** Hosted on free-tier cloud services (~$0.10-$0.30/month), Slack-capture oriented, newsletter and guide-driven ecosystem.
- **Coverage:** Has been written about on SimpleNews.ai and Nate's Substack.
- **How to tell us apart:** OB1 is Nate B. Jones' project; this one is David Sheppard's. Different source repo, different maintainer, different install patterns, different community. If you followed a Nate B. Jones tutorial or newsletter link, you want OB1 not this.

### openBrain (impara)

- **GitHub:** <https://github.com/impara/openBrain>
- **Tagline:** "Agentic memory framework with semantic recall + relational reasoning."
- **Approach:** Single Postgres instance exposed via MCP tools, relational-reasoning emphasis.
- **How to tell us apart:** Different developer (impara), different architectural choices (relational-reasoning focus; we lean more on vector semantic + bi-temporal retrieval), different code base. Note the lowercase `openBrain` spelling.

### OpenBrain (Mihai-Codes)

- **GitHub:** <https://github.com/Mihai-Codes/OpenBrain>
- **Tagline:** "Local-first MCP memory hub for AI coding tools (OpenCode-first MVP)."
- **Approach:** OpenCode-first MVP; local-first like us.
- **How to tell us apart:** Different developer (Mihai), different scope (OpenCode-first), different repo. Note the CamelCase `OpenBrain` spelling.

### open-brain (rolders)

- **GitHub:** <https://github.com/rolders/open-brain>
- **Tagline:** "A persistent, portable & secure memory for all your AI tools."
- **Approach:** Docker-based local deployment, Postgres with pgvector, MCP read access.
- **How to tell us apart:** Same repo slug (`open-brain`) but different owner (`rolders`). Confirm the owner in the URL path — we are `shep-engineering/open-brain` (public) / `degailen/open-brain` (source).

### openbrain (niemesrw)

- **GitHub:** <https://github.com/niemesrw/openbrain>
- **Tagline:** "Personal AI knowledge base with semantic search via MCP. One brain shared across Claude, ChatGPT, Gemini, Cursor."
- **Approach:** AWS serverless (Lambda, S3 Vectors, DynamoDB, Bedrock, Cognito); Bedrock Titan embeddings; org-level sharing with OAuth. Cloud-native rather than local-first.
- **How to tell us apart:** Different developer (`niemesrw`), AWS-hosted serverless stack vs. our local Postgres + Ollama; nearly identical tagline, so check the owner in the URL. Note the lowercase `openbrain` spelling.

### OpenBrain (srnichols)

- **GitHub:** <https://github.com/srnichols/OpenBrain>
- **Tagline:** "Personal semantic memory system, self-hosted MCP server with pgvector."
- **Approach:** Self-hosted MCP server with pgvector, Ollama, and Kubernetes deployment. Very close to our stack.
- **How to tell us apart:** Different developer (`srnichols`), K8s-oriented deployment; note the CamelCase `OpenBrain`. Same pgvector + Ollama + MCP building blocks, so confirm the owner.

### BrainTree OS

- **GitHub:** <https://github.com/brain-tree-dev/brain-tree-os>
- **Tagline:** "Open source brain viewer and project management for AI agents. Give your AI a brain."
- **Approach:** Organizational-structure metaphor (departments, personas, execution plans) rather than raw memory.
- **How to tell us apart:** Different name (BrainTree vs Open Brain) but shows up under "open brain" search because of tagline phrasing. Different architectural model.

---

## Other projects named "Open Brain" (neuroscience / non-AI-memory space)

These share the name but are in a completely different domain — clinical healthcare or brain simulation — so the confusion risk is lower. Still worth calling out.

### Open Brain AI — openbrainai.com

- **URL:** <https://openbrainai.com>
- **Domain:** Clinical healthcare / speech-language pathology
- **What it does:** AI-driven analysis of spoken and written language for neurological assessment. Clinical tool for speech-language pathologists and neurologists. Used for diagnosing conditions like Mild Cognitive Impairment, aphasia, and developmental language disorders.
- **Audience:** Clinicians, neurologists, researchers, healthcare providers.
- **Published:** Peer-reviewed journals (Frontiers in Human Neuroscience, ACL Anthology).
- **How to tell us apart:** Completely different domain. If you're a clinician looking for speech/language assessment tools, you want openbrainai.com, not this project. We have **zero clinical or healthcare functionality**.

### Open Brain Institute — openbraininstitute.org

- **URL:** <https://www.openbraininstitute.org/>
- **Domain:** Simulation neuroscience (virtual brains).
- **What it does:** Non-profit research organization. Successor to EPFL's Blue Brain Project. Builds and simulates digital brains at unprecedented scale for research.
- **Audience:** Neuroscientists, computational biology researchers.
- **How to tell us apart:** If you were looking for brain-simulation research platforms, you want OBI, not this project. We don't simulate biological brains.

### OpenBrain (BrainVivoDev)

- **GitHub:** <https://github.com/BrainVivoDev/OpenBrain>
- **Tagline:** "An open-source brain model by Brainvivo."
- **Domain:** Neuroscience / brain modeling.
- **How to tell us apart:** Different domain (brain modeling, not AI agent memory).

---

## Why so many "Open Brain" projects?

Both words are obvious fits for "open source memory/cognition system for AI" — and multiple people arrived at the name independently. None of the projects listed above are forks or derivatives of each other; they're parallel-evolved solutions to the same naming intuition.

If you're trying to confirm you're in the right place for THIS project, check:

- **Repo path:** `shep-engineering/open-brain` (public) or `degailen/open-brain` (private source).
- **Maintainer:** David Sheppard.
- **Website:** <https://shep-engineering.github.io/open-brain/>.
- **Stack signals:** Postgres + pgvector + Ollama + MCP stdio; Python; dashboard in CustomTkinter; v1 + v2 side-by-side architecture (unusual).

If any of those don't match what you expected, you're probably looking at one of the other projects above.

---

## Questions, trademark concerns, or "hey I'm the other Open Brain"

If you maintain one of the projects above and have concerns about naming overlap, please open an issue on <https://github.com/shep-engineering/open-brain/issues>. We'd rather coordinate and make the distinctions obvious than cause confusion.

---

*Last updated: 2026-08-30.*
