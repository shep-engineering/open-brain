"""Dashboard restart behavior."""

import dashboard


def test_dashboard_restart_uses_http_transport():
    cmd = dashboard._mcp_server_command("python.exe", "server.py")

    assert cmd == [
        "python.exe",
        "server.py",
        "--transport",
        "http",
        "--port",
        "8080",
    ]
