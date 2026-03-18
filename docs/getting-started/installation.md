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

## Step 5: Pull the embedding model

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

## Step 6: Verify

```sh
python server.py
```

The server should start without errors. Now [wire it into your AI tools](wiring-agents.md).
