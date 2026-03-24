"""
Open Brain REST API — HTTP bridge for remote clients.

Authentication: token-based pairing (no secrets shared in chat).
Tunnel: auto-starts ngrok, public URL discoverable at GET /health.

Usage:
    python rest_api.py                  # starts API + ngrok tunnel
    python rest_api.py --no-tunnel      # local only, no ngrok
    python server.py --transport rest   # same, via server.py
    python server.py --transport all    # stdio + MCP HTTP + REST + tunnel
"""
from __future__ import annotations

# ── MUST BE FIRST: Fake MCP so `import server` doesn't block ────────────────
# FastMCP's constructor sets up stdio transport which blocks forever.
# We replace it with a no-op before importing server.py.
import os
import sys
import types as _types

_fake_fastmcp = _types.ModuleType("mcp.server.fastmcp")
class _DummyMCP:
    def __init__(self, *a, **kw): pass
    def tool(self, *a, **kw):
        def decorator(fn): return fn
        return decorator
    def run(self, *a, **kw): pass
    def streamable_http_app(self, *a, **kw): return None
_fake_fastmcp.FastMCP = _DummyMCP

for _mod_name in ["mcp", "mcp.server", "mcp.server.fastmcp"]:
    sys.modules[_mod_name] = _fake_fastmcp if _mod_name == "mcp.server.fastmcp" else _types.ModuleType(_mod_name)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import server as ob  # Now safe — FastMCP is a no-op

# ── Regular imports ──────────────────────────────────────────────────────────

import hashlib
import json
import secrets
import subprocess
import threading
import time
from typing import Optional

import urllib.request
import urllib.error

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# ─── Config ──────────────────────────────────────────────────────────────────

REST_PORT = int(os.getenv("OPEN_BRAIN_REST_PORT", "8765"))
REST_HOST = os.getenv("OPEN_BRAIN_REST_HOST", "0.0.0.0")
TOKEN_TTL_HOURS = int(os.getenv("OPEN_BRAIN_TOKEN_TTL_HOURS", "24"))
ENABLE_TUNNEL = os.getenv("OPEN_BRAIN_TUNNEL", "true").lower() in ("true", "1", "yes")

# Master signing key — auto-generated, never exposed
_MASTER_KEY_ENV = "OPEN_BRAIN_API_KEY"
_master_key = os.getenv(_MASTER_KEY_ENV, "")

if not _master_key:
    _master_key = secrets.token_urlsafe(32)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    with open(env_path, "a") as f:
        f.write(f"\n{_MASTER_KEY_ENV}={_master_key}\n")
    print("  [open-brain] Master key generated and saved to .env")

# ─── Tunnel state ────────────────────────────────────────────────────────────

_tunnel_url: Optional[str] = None
_tunnel_process: Optional[subprocess.Popen] = None


def _find_ngrok() -> Optional[str]:
    """Find ngrok binary. Returns path or None."""
    # Try PATH first
    try:
        result = subprocess.run(["ngrok", "version"], capture_output=True, timeout=5)
        if result.returncode == 0:
            return "ngrok"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try common Windows install locations
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Links", "ngrok.exe"),
        os.path.join(home, "AppData", "Local", "ngrok", "ngrok.exe"),
    ]
    # Also search WinGet packages directory
    winget_dir = os.path.join(home, "AppData", "Local", "Microsoft", "WinGet", "Packages")
    if os.path.isdir(winget_dir):
        for d in os.listdir(winget_dir):
            if "ngrok" in d.lower():
                candidate = os.path.join(winget_dir, d, "ngrok.exe")
                candidates.append(candidate)

    for path in candidates:
        if os.path.isfile(path):
            return path

    return None


def _check_ngrok_auth(ngrok_bin: str) -> bool:
    """Check if ngrok has an auth token configured."""
    # Check if config file exists with an authtoken
    config_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ngrok", "ngrok.yml")
    if not os.path.exists(config_path):
        return False
    try:
        with open(config_path, "r") as f:
            content = f.read()
            return "authtoken" in content
    except Exception:
        return False


def _start_tunnel(port: int) -> Optional[str]:
    """Start ngrok tunnel and return the public URL."""
    global _tunnel_url, _tunnel_process

    # Step 1: Find ngrok
    ngrok_bin = _find_ngrok()
    if not ngrok_bin:
        print("  [open-brain] ERROR: ngrok not found.", file=sys.stderr)
        print("  [open-brain] Install it: winget install ngrok.ngrok", file=sys.stderr)
        print("  [open-brain] Then restart your shell and try again.", file=sys.stderr)
        return None

    # Step 2: Check auth token
    if not _check_ngrok_auth(ngrok_bin):
        print("  [open-brain] ERROR: ngrok auth token not configured.", file=sys.stderr)
        print("  [open-brain] Steps to fix:", file=sys.stderr)
        print("  [open-brain]   1. Sign up free: https://dashboard.ngrok.com/signup", file=sys.stderr)
        print("  [open-brain]   2. Get token: https://dashboard.ngrok.com/get-started/your-authtoken", file=sys.stderr)
        print("  [open-brain]   3. Run: ngrok config add-authtoken YOUR_TOKEN", file=sys.stderr)
        print("  [open-brain]   4. Restart Open Brain", file=sys.stderr)
        return None

    # Step 3: Start ngrok (use full path, don't rely on PATH)
    print(f"  [open-brain] Using ngrok at: {ngrok_bin}")
    _tunnel_process = subprocess.Popen(
        [ngrok_bin, "http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Step 4: Poll for the tunnel URL
    for attempt in range(20):
        time.sleep(1)
        try:
            req = urllib.request.Request("http://localhost:4040/api/tunnels")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                tunnels = data.get("tunnels", [])
                for t in tunnels:
                    if t.get("proto") == "https":
                        _tunnel_url = t["public_url"]
                        return _tunnel_url
                if tunnels:
                    _tunnel_url = tunnels[0]["public_url"]
                    return _tunnel_url
        except Exception:
            continue

    # Step 5: If we got here, something went wrong — check stderr
    if _tunnel_process and _tunnel_process.poll() is not None:
        stderr_out = _tunnel_process.stderr.read().decode(errors="replace") if _tunnel_process.stderr else ""
        print(f"  [open-brain] ERROR: ngrok exited with code {_tunnel_process.returncode}", file=sys.stderr)
        if "authtoken" in stderr_out.lower():
            print("  [open-brain] Auth token issue. Run:", file=sys.stderr)
            print(f"  [open-brain]   {ngrok_bin} config add-authtoken YOUR_TOKEN", file=sys.stderr)
        elif stderr_out:
            for line in stderr_out.strip().split("\n")[-5:]:
                print(f"  [open-brain]   {line}", file=sys.stderr)
    else:
        print("  [open-brain] ERROR: ngrok started but no tunnel URL after 20s.", file=sys.stderr)
        print("  [open-brain] Check: http://localhost:4040", file=sys.stderr)
    if _tunnel_process:
        _tunnel_process.terminate()
        _tunnel_process = None
    return None


def _stop_tunnel():
    """Stop the ngrok tunnel."""
    global _tunnel_process, _tunnel_url
    if _tunnel_process:
        _tunnel_process.terminate()
        _tunnel_process = None
    _tunnel_url = None


# ─── Session token store ─────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}


def _create_token(client_name: str) -> str:
    raw = secrets.token_urlsafe(32)
    sig = hashlib.sha256(f"{raw}{_master_key}".encode()).hexdigest()[:16]
    token = f"ob_{raw}_{sig}"
    now = time.time()
    _sessions[token] = {
        "client_name": client_name,
        "created_at": now,
        "expires_at": now + TOKEN_TTL_HOURS * 3600,
    }
    return token


def _verify_token(token: str) -> dict:
    session = _sessions.get(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session token. Pair first via POST /pair")
    if time.time() > session["expires_at"]:
        del _sessions[token]
        raise HTTPException(status_code=401, detail="Session expired. Re-pair via POST /pair")
    return session


# ─── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Open Brain REST API",
    description=(
        "HTTP bridge to Open Brain for remote AI clients.\n\n"
        "**Auth flow:** POST /pair -> get session token -> use X-Session-Token header.\n"
        "**Discovery:** GET /health returns the public tunnel URL."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

_token_header = APIKeyHeader(name="X-Session-Token", auto_error=False)


async def _auth(token: Optional[str] = Security(_token_header)):
    if not token:
        raise HTTPException(status_code=401, detail="Missing X-Session-Token header. Pair first via POST /pair")
    return _verify_token(token)


# ─── Pairing ─────────────────────────────────────────────────────────────────

class PairRequest(BaseModel):
    client_name: str

class PairResponse(BaseModel):
    session_token: str
    expires_in_hours: int
    public_url: Optional[str]
    message: str


@app.post("/pair", response_model=PairResponse)
async def pair(req: PairRequest, request: Request):
    """Pair a remote client. Returns a session token and the public URL."""
    client_ip = request.client.host if request.client else "unknown"
    token = _create_token(req.client_name)

    print(f"  [open-brain] PAIRED: {req.client_name} from {client_ip}")
    print(f"  [open-brain]   Token expires in {TOKEN_TTL_HOURS}h | Active: {len(_sessions)}")

    return PairResponse(
        session_token=token,
        expires_in_hours=TOKEN_TTL_HOURS,
        public_url=_tunnel_url,
        message=f"Paired as '{req.client_name}'. Use X-Session-Token header for all requests.",
    )


@app.post("/pair/renew")
async def renew(session: dict = Security(_auth)):
    old_token = None
    for t, s in _sessions.items():
        if s is session:
            old_token = t
            break
    if old_token:
        del _sessions[old_token]
    new_token = _create_token(session["client_name"])
    print(f"  [open-brain] RENEWED: {session['client_name']}")
    return {"session_token": new_token, "expires_in_hours": TOKEN_TTL_HOURS}


@app.get("/pair/status")
async def pair_status(session: dict = Security(_auth)):
    remaining = session["expires_at"] - time.time()
    return {
        "client_name": session["client_name"],
        "expires_in_seconds": int(remaining),
        "expires_in_hours": round(remaining / 3600, 1),
    }


# ─── Request models ──────────────────────────────────────────────────────────

class RememberRequest(BaseModel):
    content: str
    source: str = "rest-api"
    type_override: str = ""
    project: str = ""

class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    type_filter: str = ""
    project: str = ""
    people_filter: Optional[list[str]] = None
    source: str = ""

class CaptureRequest(BaseModel):
    context: str
    source: str = "rest-api"
    project: str = ""

class AnnotateRequest(BaseModel):
    note: str = ""
    clear: bool = False

class RateRequest(BaseModel):
    direction: str

class PruneRequest(BaseModel):
    days: int = 90
    min_access: int = 0
    dry_run: bool = True

class ForgetManyRequest(BaseModel):
    memory_ids: list[int]


def _parse(result: str):
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return {"result": result}


# ─── Discovery ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health + discovery. Returns the public tunnel URL so clients can find us."""
    return {
        "status": "ok",
        "service": "open-brain",
        "version": "1.0.0",
        "public_url": _tunnel_url,
        "local_url": f"http://localhost:{REST_PORT}",
        "active_sessions": len(_sessions),
        "pair_endpoint": "/pair",
    }


# ─── API endpoints ───────────────────────────────────────────────────────────

@app.post("/remember", dependencies=[Security(_auth)])
async def remember(req: RememberRequest):
    return _parse(ob.remember(content=req.content, source=req.source, type_override=req.type_override, project=req.project))

@app.post("/search", dependencies=[Security(_auth)])
async def search(req: SearchRequest):
    return _parse(ob.search(query=req.query, limit=req.limit, type_filter=req.type_filter, project=req.project, people_filter=req.people_filter, source=req.source))

@app.get("/recall/{memory_id}", dependencies=[Security(_auth)])
async def recall(memory_id: int):
    return _parse(ob.recall(memory_id=memory_id))

@app.get("/list", dependencies=[Security(_auth)])
async def list_recent(limit: int = 20, days: int = 0):
    return _parse(ob.list_recent(limit=limit, days=days))

@app.get("/stats", dependencies=[Security(_auth)])
async def stats():
    return _parse(ob.stats())

@app.post("/capture", dependencies=[Security(_auth)])
async def capture_context(req: CaptureRequest):
    return _parse(ob.capture_context(context=req.context, source=req.source, project=req.project))

@app.post("/annotate/{memory_id}", dependencies=[Security(_auth)])
async def annotate(memory_id: int, req: AnnotateRequest):
    return _parse(ob.annotate(memory_id=memory_id, note=req.note, clear=req.clear))

@app.post("/rate/{memory_id}", dependencies=[Security(_auth)])
async def rate(memory_id: int, req: RateRequest):
    return _parse(ob.rate(memory_id=memory_id, direction=req.direction))

@app.post("/prune", dependencies=[Security(_auth)])
async def prune(req: PruneRequest):
    return _parse(ob.prune(days=req.days, min_access=req.min_access, dry_run=req.dry_run))

@app.delete("/forget/{memory_id}", dependencies=[Security(_auth)])
async def forget(memory_id: int):
    return _parse(ob.forget(memory_id=memory_id))

@app.post("/forget-many", dependencies=[Security(_auth)])
async def forget_many(req: ForgetManyRequest):
    return _parse(ob.forget_many(memory_ids=req.memory_ids))

@app.post("/pin/{memory_id}", dependencies=[Security(_auth)])
async def pin(memory_id: int):
    return _parse(ob.pin(memory_id=memory_id))

@app.post("/unpin/{memory_id}", dependencies=[Security(_auth)])
async def unpin(memory_id: int):
    return _parse(ob.unpin(memory_id=memory_id))


@app.get("/export", dependencies=[Security(_auth)])
async def export_memories(limit: int = 500, days: int = 0):
    """Export memories as JSON for offline sharing (e.g. uploading to Co-work).

    Returns a self-contained JSON array that can be saved to a file
    and uploaded to any AI tool that accepts file context.
    """
    result = ob.list_recent(limit=limit, days=days)
    memories = json.loads(result)

    # Enrich each memory with full content via recall
    enriched = []
    for mem in memories:
        try:
            full = json.loads(ob.recall(memory_id=mem["id"]))
            enriched.append(full)
        except Exception:
            enriched.append(mem)

    return {
        "service": "open-brain",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(enriched),
        "memories": enriched,
    }


# ─── TODO: MCP Registry ─────────────────────────────────────────────────────
# When ready to productionalize:
# 1. Create an MCP connector manifest (name, description, auth config)
# 2. Submit to the MCP registry for listing
# 3. This allows Claude Co-work and other sandboxed AI tools to connect
#    natively without ngrok (Co-work's egress proxy blocks ngrok domains)
# 4. The registry lists connection config only — source code stays private
# See: https://modelcontextprotocol.io/docs/registry


# ─── Entry point ─────────────────────────────────────────────────────────────

def _delayed_tunnel_start(port: int):
    """Start the tunnel after a delay to let uvicorn bind first."""
    time.sleep(3)  # Wait for uvicorn to be ready
    print("\n  [open-brain] Starting ngrok tunnel...")
    url = _start_tunnel(port)
    if url:
        print(f"\n  [open-brain] ==========================================")
        print(f"  [open-brain] PUBLIC URL: {url}")
        print(f"  [open-brain] Docs: {url}/docs")
        print(f"  [open-brain] Pair: POST {url}/pair")
        print(f"  [open-brain] ==========================================\n")
    else:
        print(f"  [open-brain] No tunnel. Local access only: http://localhost:{port}")


def run(host: str = REST_HOST, port: int = REST_PORT, tunnel: bool = ENABLE_TUNNEL):
    import uvicorn

    if tunnel:
        # Start tunnel in background thread AFTER uvicorn starts
        t = threading.Thread(target=_delayed_tunnel_start, args=(port,), daemon=True)
        t.start()
    else:
        print(f"\n  [open-brain] Tunnel disabled. Local only: http://localhost:{port}")

    print(f"  [open-brain] Clients self-pair at POST /pair. No secrets to share.\n")

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        _stop_tunnel()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Open Brain REST API")
    parser.add_argument("--no-tunnel", action="store_true", help="Disable ngrok tunnel")
    parser.add_argument("--port", type=int, default=REST_PORT)
    parser.add_argument("--host", default=REST_HOST)
    args = parser.parse_args()
    run(host=args.host, port=args.port, tunnel=not args.no_tunnel)
