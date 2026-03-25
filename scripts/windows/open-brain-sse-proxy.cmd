@echo off
pushd F:\open-brain
start "" /min F:\open-brain\.venv\Scripts\python.exe -m mcp.server.sse --port 8765 -- python server.py
popd
