# Persistent HTTP transport for the brain MCP server — PROPOSAL
*2026-09-12. Decision + scoping doc. Branch-and-hold. Prior art already exists on
`feat/http-transport-v24-3` (commit 35acf6d) — this is mostly a rebase + deploy, not greenfield.*

## The problem (Shep's question)
Today the brain MCP server is a **per-client stdio subprocess**: Claude Code (and Cursor/Windsurf)
each spawn `brain_v2/server.py` fresh over stdio when they connect. Consequences:
- A brain CODE change (new tool, gate fix) does NOT reach a running client until that client respawns
  the server. Tonight's `supersede_fact_v2` tool is on `main` but won't appear in this session until a
  reconnect/reboot — the schema ALTER went live instantly only because the DB is already a standing
  service.
- Every client runs its OWN copy of the server process. No single source of truth at runtime.
- Contrast: Anthropic's model and the Postgres DB are **standing services** behind a boundary — deploy
  once, every caller gets it on the next request, no client restart. The brain should work the same way.

## The upgrade
Run the brain as a **persistent HTTP (streamable-HTTP) MCP service** — one long-lived process (like the
Postgres container) that all clients connect to over `http://host:port/mcp`. Then:
- Updating the brain = restart (or hot-reload) that ONE service; every client picks it up on its next
  request. No editor/client restart, ever. (Exactly the property Shep wants, and needs for another project.)
- One runtime source of truth; centralized logging/observability already in `observability.py`.
- Enables remote/LAN clients and non-editor consumers (the "another project" use case).

## Prior art (do NOT rebuild — rebase it)
`feat/http-transport-v24-3` @ 35acf6d already implemented the transport in `brain_v2/server.py`:
- `mcp.streamable_http_app()` + a uvicorn runner (`_run_http`).
- `--transport {stdio,http,both}` CLI flag (stdio for editors, http for the service, both for dev).
- Env config `OPEN_BRAIN_V2_PORT` (8081) / `OPEN_BRAIN_V2_HOST` (0.0.0.0).
- Session auto-register runs in a daemon thread so HTTP startup isn't blocked.
That branch forked BEFORE the four v0.28.0 fixes (its diff vs main shows my files as "missing"), so it is
stale — the work is to carry those ~30 lines forward onto current main, not re-author them.

## Scope of work (ordered)
1. **Rebase the transport code** from 35acf6d onto current `main` (cherry-pick the server.py transport
   block + argparse `__main__`; discard the branch's stale everything-else). Small, mechanical.
2. **Dependencies**: add `uvicorn` (+ starlette, pulled by mcp) to requirements; confirm the installed
   `mcp` exposes `streamable_http_app()` (the API the code calls).
3. **Deployment layer** (the actually-new part): run the HTTP server as a standing service alongside the
   DB — a `docker-compose.v2.yml` service or a supervised process — with health check on `/mcp`,
   restart policy, and the observability log wired. Decide host binding (localhost vs LAN) + auth posture
   for anything beyond localhost (streamable-HTTP has no auth by default — a token/reverse-proxy is
   REQUIRED before LAN/remote exposure; call this out as a security gate).
4. **Client config**: switch `.mcp`/editor configs from the stdio spawn to the HTTP URL
   (`{"type":"http","url":"http://127.0.0.1:8081/mcp"}`), keeping a stdio fallback documented.
5. **Cutover + reload story**: document "update brain = restart the service" and the `/mcp` reconnect
   path; verify a code change reaches a connected client after a service restart WITHOUT restarting the
   client.

## Validator loop (when built — separate project)
- PLAN gate: is streamable-HTTP the right MCP transport for this mcp version? auth/exposure model sound?
  does session-registry liveness (pid-based) still make sense when the server is one shared process, not
  per-client? (Likely needs rework — a shared server has ONE pid but many logical clients.)
- EXECUTE on its own branch, branch-and-hold.
- DIFF gate + real run: start the service, connect two clients, prove a mid-flight code update reaches
  both after one service restart; full brain_v2 suite still green over HTTP.

## Risks / open questions
- **Session-registry model**: pid-lifecycle liveness assumes one process per session. A shared HTTP
  server breaks that assumption — needs a per-connection session identity instead. This is the main
  design question, not the transport plumbing.
- **Auth**: no exposure beyond localhost without a real auth layer. Hard gate.
- **Concurrency**: stdio is single-client-serial; HTTP is concurrent — confirm the psycopg2
  connection-singleton in store.py is safe under concurrent requests (it likely is NOT — a shared single
  connection across concurrent HTTP handlers needs a pool). **This is a real correctness item**, flag it.

## Recommendation
Worth doing, and cheap on the transport itself (code exists). The genuine work is the deployment/service
layer, the session-identity rework, and the connection-pool safety under concurrency. Scope it as its own
project (like the KG). Prior art on 35acf6d is the starting point.

## Status
PROPOSAL ONLY. Nothing built. Awaiting Shep's go to start it as a scoped project.
