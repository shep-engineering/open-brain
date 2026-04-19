# ChatGPT Desktop

ChatGPT Desktop's MCP support requires a remote endpoint. It doesn't support local stdio servers directly. Here's how to connect it.

---

## Option A: Local SSE Proxy (Recommended)

The `mcp` package can expose a stdio server over SSE (Server-Sent Events):

```sh
.venv\Scripts\python.exe -m mcp.server.sse --port 8765 -- python server.py
```

Then in ChatGPT Desktop:

1. Open **Settings** > **Connected Apps** > **Add MCP Server**
2. Enter: `http://localhost:8765/sse`

This process must be running whenever you use ChatGPT Desktop with the brain.

---

## Option B: Tunnel via ngrok

For access from other devices (e.g. ChatGPT mobile):

```sh
# 1. Install ngrok
winget install ngrok

# 2. Start the SSE proxy
.venv\Scripts\python.exe -m mcp.server.sse --port 8765 -- python server.py

# 3. Tunnel it
ngrok http 8765

# 4. Use the https://xxxx.ngrok.io/sse URL in ChatGPT Desktop
```

---

## Auto-Capture Instructions

ChatGPT Desktop uses Custom Instructions for persistent behavior:

1. Open **Settings** (gear icon) > **Personalization** > **Custom Instructions**
2. Paste the contents of `prompts/generic-system-prompt.md` into the "What would you like ChatGPT to know?" field

This tells ChatGPT to silently capture and recall memories using the Open Brain tools.

---

## v2 via SSE

To connect ChatGPT Desktop to brain_v2, run a second SSE proxy on a different port:

```sh
.venv\Scripts\python.exe -m mcp.server.sse --port 8766 -- python brain_v2/server.py
```

Then add `http://localhost:8766/sse` as a second MCP server in ChatGPT Desktop settings.
