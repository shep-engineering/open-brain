# Further Reading

Background and related reading on agent memory, MCP, and the problem
space Open Brain operates in.

## Agent memory

- **[Agent Memory: Why Your AI Has Amnesia and How to Fix It](https://blogs.oracle.com/developers/agent-memory-why-your-ai-has-amnesia-and-how-to-fix-it)** — Casius Lee (Oracle Developers Blog, Feb 2026). Breaks down the four memory types (working, procedural, semantic, episodic), why bigger context windows aren't a substitute for persistent memory, and why agent memory is fundamentally a database problem.

## Anthropic engineering on agentic harnesses

- **[Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)** — Anthropic Engineering (April 2026). Multi-agent (Planner / Generator / Evaluator) patterns, context resets vs. compaction, and the principle that harnesses encode assumptions that go stale as models improve.
- **[Claude Managed Agents](https://www.anthropic.com/engineering/managed-agents)** — Anthropic Engineering (April 2026). Architecture of a durable event-log session, stateless harness processes, and stable interfaces (`execute`, `getEvents`, `wake`) that decouple the session/harness/sandbox trio.

## Protocol

- **[Model Context Protocol](https://modelcontextprotocol.io)** — The open standard every Open Brain tool speaks. Any MCP-compatible client (Claude Code, Cursor, Windsurf, ChatGPT Desktop) can connect.
