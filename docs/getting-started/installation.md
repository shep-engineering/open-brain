# Installation

Get Open Brain running in ~15 minutes.

---

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| **Python 3.10+** | Runs the MCP server | [python.org](https://python.org) |
| **Docker Desktop** | Hosts PostgreSQL + pgvector | [docker.com](https://docker.com) |
| **Ollama** | Local embeddings (free) | [ollama.com](https://ollama.com) |

---

## Step 1: Clone and create a virtual environment

```sh
git clone https://github.com/shep-engineering/open-brain.git
cd open-brain
python -m venv .venv
```

Activate it:

=== "macOS / Linux"

    ```sh
    source .venv/bin/activate
    ```

=== "Windows"

    ```sh
    .venv\Scripts\activate
    ```

Install dependencies:

```sh
pip install -r requirements.txt
```

---

## Step 2: Configure environment

```sh
cp .env.example .env
```

On Windows: `copy .env.example .env`

Edit `.env`. The defaults work out of the box for local Ollama + Docker.

!!! note "WSL + Windows Ollama"
    If you run Ollama on Windows and the MCP server from WSL, enable mirrored networking in `C:\Users\<USERNAME>\.wslconfig`:

    ```ini
    [wsl2]
    localhostForwarding=true
    networkingMode=mirrored
    ```

    Then run `wsl --shutdown` and restart WSL.

---

## Step 3: Start PostgreSQL

```sh
docker compose up -d
```

Verify with `docker ps`. You should see `open-brain-db` running.

---

## Step 4: Initialize the database

```sh
python scripts/setup_db.py
```

This creates the `memories` table, pgvector extension, HNSW index, and all supporting indexes.

---

## Step 5: Start brain_v2 database (recommended)

v2 runs on a separate Postgres container alongside v1:

```sh
docker compose -f docker-compose.v2.yml up -d
```

This starts `open-brain-v2-db` on port **5433** with database `open_brain_v2`. The schema is applied automatically on first `boot_session_v2` call.

## Step 6: Set up the test database (optional)

Open Brain uses a separate Docker container for tests so they never touch your production data.

```sh
docker compose -f docker-compose.test.yml up -d
```

This starts `open-brain-test-db` on port **5434** with database `openbrain_test`. No persistent volume — test data is wiped on container removal.

Run tests:

```sh
pytest tests/ -v
```

See the full [Testing guide](../guides/testing.md) for details on safety layers, fake embeddings, and troubleshooting.

---

## Step 7: Pull the embedding model

```sh
ollama pull nomic-embed-text
```

Optional but recommended. Pull a metadata LLM for richer extraction:

```sh
ollama pull qwen2.5:32b
```

!!! tip "Dual model setup"
    If you use both `nomic-embed-text` and a metadata model, start Ollama with `OLLAMA_MAX_LOADED_MODELS=2` to avoid repeated model evictions.

---

## Step 8: Verify

```sh
python server.py
```

The server should start without errors. Now [wire it into your AI tools](wiring-agents.md).

---

## Step 9: Launch the Dashboard (Windows)

Open Brain includes a dark-themed monitoring dashboard. It auto-detects whether services are running and starts them if needed.

```sh
.venv\Scripts\pythonw.exe dashboard.py
```

To create Desktop shortcuts (ON, OFF, SSE Proxy, Dashboard):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\create-desktop-shortcuts.ps1
```

See the full [Dashboard guide](../guides/dashboard.md) for details on event-driven refresh, observability, and troubleshooting.
