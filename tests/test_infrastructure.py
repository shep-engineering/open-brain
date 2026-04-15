"""Unit tests for scripts/infrastructure.py — mocked subprocess + urllib.

Covers:
- Progress callback routing
- ensure_docker success / cold-start / timeout
- ensure_db success / docker start failure / readiness timeout
- ensure_ollama already-up / cold-start / launch-failure / timeout
- stop_all best-effort semantics

Live integration (real processes) lives in test_infrastructure_live.py.
"""

import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import infrastructure  # noqa: E402


# ---------- Progress ----------

class TestProgress:
    def test_format_includes_step_status_elapsed(self):
        p = infrastructure.Progress(
            step="docker", status="ready", detail="ok", elapsed_s=1.23
        )
        s = p.format()
        assert "docker:ready" in s
        assert "t=1.23s" in s
        assert "ok" in s


# ---------- Infrastructure init ----------

class TestInfraInit:
    def test_creates_logs_dir(self, tmp_path):
        infra = infrastructure.Infrastructure(logs_dir=tmp_path / "logs")
        assert (tmp_path / "logs").is_dir()

    def test_noop_callback_default(self, tmp_path):
        # Should not raise even with no callback
        infra = infrastructure.Infrastructure(logs_dir=tmp_path / "logs")
        infra._emit("docker", "info", "hello")


# ---------- ensure_docker ----------

class TestEnsureDocker:
    def _make(self, tmp_path):
        calls = []
        infra = infrastructure.Infrastructure(
            on_progress=lambda p: calls.append(p),
            logs_dir=tmp_path / "logs",
        )
        return infra, calls

    def test_fast_path_when_docker_already_running(self, tmp_path):
        infra, calls = self._make(tmp_path)
        with patch.object(infra, "_docker_info_ok", return_value=True):
            assert infra.ensure_docker() is True
        statuses = [(c.step, c.status) for c in calls]
        assert ("docker", "ready") in statuses

    def test_timeout_when_daemon_never_responds(self, tmp_path, monkeypatch):
        infra, calls = self._make(tmp_path)
        # Point DOCKER_DESKTOP_EXE at a non-existent path so the launch
        # branch is skipped; test purely the poll→fail timeout behavior.
        monkeypatch.setattr(
            infrastructure, "DOCKER_DESKTOP_EXE", Path(r"C:\__nonexistent__\DD.exe")
        )
        monkeypatch.setattr(infrastructure.time, "sleep", lambda s: None)
        with patch.object(infra, "_docker_info_ok", return_value=False):
            assert infra.ensure_docker(timeout=1) is False
        statuses = [(c.step, c.status) for c in calls]
        assert ("docker", "fail") in statuses

    def test_launches_desktop_when_exe_present_and_not_running(self, tmp_path, monkeypatch):
        infra, calls = self._make(tmp_path)
        ok_after_launch = [False]

        def docker_ok():
            # First call: not OK. After Popen called: OK.
            return ok_after_launch[0]

        popen_calls = []

        class FakePopen:
            def __init__(self, *args, **kwargs):
                popen_calls.append((args, kwargs))
                ok_after_launch[0] = True

        monkeypatch.setattr(infrastructure.time, "sleep", lambda s: None)
        with patch.object(infrastructure, "IS_WINDOWS", True), \
             patch.object(infra, "_docker_info_ok", side_effect=docker_ok), \
             patch("pathlib.Path.exists", return_value=True), \
             patch.object(infrastructure.subprocess, "Popen", FakePopen):
            assert infra.ensure_docker(timeout=5) is True
        assert popen_calls, "Docker Desktop should have been launched"


# ---------- ensure_db ----------

class TestEnsureDb:
    def _make(self, tmp_path):
        calls = []
        infra = infrastructure.Infrastructure(
            on_progress=lambda p: calls.append(p),
            logs_dir=tmp_path / "logs",
        )
        return infra, calls

    def test_success_when_docker_start_ok_and_postgres_reachable(self, tmp_path):
        infra, calls = self._make(tmp_path)
        with patch.object(infrastructure.subprocess, "run") as mock_run, \
             patch.object(infra, "_db_reachable", return_value=True):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            assert infra.ensure_db() is True

    def test_fails_when_docker_start_nonzero(self, tmp_path):
        infra, calls = self._make(tmp_path)
        with patch.object(infrastructure.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="container not found")
            assert infra.ensure_db() is False
        statuses = [(c.step, c.status) for c in calls]
        assert ("db", "fail") in statuses

    def test_fails_when_postgres_never_reachable(self, tmp_path, monkeypatch):
        infra, calls = self._make(tmp_path)
        monkeypatch.setattr(infrastructure.time, "sleep", lambda s: None)
        with patch.object(infrastructure.subprocess, "run") as mock_run, \
             patch.object(infra, "_db_reachable", return_value=False):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            assert infra.ensure_db(timeout=1) is False


# ---------- ensure_ollama ----------

class TestEnsureOllama:
    def _make(self, tmp_path):
        calls = []
        infra = infrastructure.Infrastructure(
            on_progress=lambda p: calls.append(p),
            logs_dir=tmp_path / "logs",
        )
        return infra, calls

    def test_fast_path_when_already_responsive(self, tmp_path):
        infra, calls = self._make(tmp_path)
        with patch.object(infra, "_ollama_api_ok", return_value=True):
            assert infra.ensure_ollama() is True
        popen_count = 0  # no spawn should happen
        # nothing to assert here except return value and callback flow
        assert any(c.status == "ready" for c in calls)

    def test_cold_spawn_then_ready(self, tmp_path, monkeypatch):
        infra, calls = self._make(tmp_path)
        ok_sequence = iter([False, True])  # fail once, then ready

        def api_ok():
            try:
                return next(ok_sequence)
            except StopIteration:
                return True

        popen_calls = []

        class FakePopen:
            pid = 99999  # ollama pid gets written to ollama.pid after spawn

            def __init__(self, *args, **kwargs):
                popen_calls.append(kwargs)

        monkeypatch.setattr(infrastructure.time, "sleep", lambda s: None)
        with patch.object(infra, "_ollama_api_ok", side_effect=api_ok), \
             patch.object(infrastructure.subprocess, "Popen", FakePopen):
            assert infra.ensure_ollama(timeout=5) is True

        assert popen_calls, "Popen should have been called for cold start"
        kwargs = popen_calls[0]
        # Verify the CRITICAL spawn parameters
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.STDOUT
        assert kwargs["close_fds"] is True
        if infrastructure.IS_WINDOWS:
            # Must NOT include DETACHED_PROCESS — it would block Ctrl+Break.
            # Must include CREATE_NEW_CONSOLE + CREATE_NEW_PROCESS_GROUP and
            # a STARTUPINFO that hides the console window.
            flags = kwargs["creationflags"]
            assert not (flags & subprocess.DETACHED_PROCESS), \
                "DETACHED_PROCESS must not be set — breaks graceful shutdown"
            assert flags & subprocess.CREATE_NEW_CONSOLE, \
                "CREATE_NEW_CONSOLE required for hidden console + Ctrl+Break"
            assert flags & subprocess.CREATE_NEW_PROCESS_GROUP, \
                "CREATE_NEW_PROCESS_GROUP required for targeted signaling"
            si = kwargs["startupinfo"]
            assert si.wShowWindow == subprocess.SW_HIDE, \
                "console window must be hidden (SW_HIDE)"
        else:
            assert kwargs["start_new_session"] is True

    def test_launch_failure_when_ollama_not_on_path(self, tmp_path):
        infra, calls = self._make(tmp_path)
        with patch.object(infra, "_ollama_api_ok", return_value=False), \
             patch.object(infrastructure.subprocess, "Popen", side_effect=FileNotFoundError("no ollama")):
            assert infra.ensure_ollama(timeout=1) is False
        statuses = [(c.step, c.status) for c in calls]
        assert ("ollama", "fail") in statuses


# ---------- stop_all ----------

class TestStopAll:
    def test_externally_managed_ollama_is_not_killed(self, tmp_path):
        """When there is no ollama.pid file, ollama is assumed to be
        externally managed (Windows desktop app, systemd, etc.). We must
        unload models but NEVER issue a process kill — the external
        manager would respawn, and we'd be rude to any other consumer."""
        calls = []
        infra = infrastructure.Infrastructure(
            on_progress=lambda p: calls.append(p),
            logs_dir=tmp_path / "logs",
        )
        assert not infra.ollama_pid_file.exists()  # precondition
        with patch.object(infrastructure.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            infra.stop_all()

        invoked = [c.args[0] for c in mock_run.call_args_list]
        first_tokens = [a[0] for a in invoked]
        # docker stop open-brain-db must still run
        assert "docker" in first_tokens
        docker_cmds = [a for a in invoked if a[0] == "docker"]
        assert any("open-brain-db" in a for a in docker_cmds)
        # Must enumerate loaded models
        ollama_cmds = [a for a in invoked if a[0] == "ollama"]
        assert any(a[1] == "ps" for a in ollama_cmds)
        # MUST NOT kill ollama process when externally managed
        if infrastructure.IS_WINDOWS:
            taskkills = [a for a in invoked if a[0] == "taskkill"]
            ollama_kills = [a for a in taskkills if any("ollama" in tok.lower() for tok in a)]
            assert not ollama_kills, f"must not kill externally-managed ollama: {ollama_kills}"
            dd_kills = [a for a in invoked if "Docker Desktop.exe" in a]
            assert not dd_kills, f"must not kill Docker Desktop: {dd_kills}"
        else:
            pkills = [a for a in invoked if a[0] == "pkill"]
            assert not pkills, f"must not pkill externally-managed ollama: {pkills}"
        # And a progress entry must explicitly flag the externally-managed case
        statuses = [c.detail for c in calls]
        assert any("externally managed" in s for s in statuses), \
            f"expected 'externally managed' log line, got {statuses}"

    def test_owned_ollama_gets_graceful_signal(self, tmp_path):
        """When ollama.pid file is present and the pid is 'alive', we
        must deliver a graceful shutdown: on Windows that's the Ctrl+Break
        helper subprocess; on POSIX it's SIGTERM via os.kill."""
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "ollama.pid").write_text("12345", encoding="utf-8")
        calls = []
        infra = infrastructure.Infrastructure(
            on_progress=lambda p: calls.append(p),
            logs_dir=logs,
        )
        with patch.object(infrastructure.subprocess, "run") as mock_run, \
             patch.object(infrastructure.os, "kill") as mock_kill, \
             patch.object(infra, "_win_send_ctrl_break", return_value=True) as mock_break, \
             patch.object(infra, "_pid_is_alive", side_effect=[True, False]):
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            infra.stop_all()

        if infrastructure.IS_WINDOWS:
            assert mock_break.called, "expected Ctrl+Break helper to be invoked"
            assert mock_break.call_args.args == (12345,)
            # os.kill must NOT be used for the graceful step on Windows
            # (it doesn't reliably deliver CTRL_BREAK across consoles).
            assert not mock_kill.called or \
                all(c.args[1] != infrastructure.signal.CTRL_BREAK_EVENT
                    for c in mock_kill.call_args_list)
        else:
            assert mock_kill.called, "expected os.kill(pid, SIGTERM) on POSIX"
            sent_pid, sent_sig = mock_kill.call_args.args
            assert sent_pid == 12345
            assert sent_sig == infrastructure.signal.SIGTERM
        assert not (logs / "ollama.pid").exists()

    def test_never_raises_on_missing_binaries(self, tmp_path):
        infra = infrastructure.Infrastructure(logs_dir=tmp_path / "logs")
        with patch.object(infrastructure.subprocess, "run", side_effect=FileNotFoundError):
            # Must not raise
            infra.stop_all()


# ---------- bring_up short-circuit ----------

class TestBringUp:
    def test_returns_false_if_docker_fails(self):
        with patch.object(infrastructure.Infrastructure, "ensure_docker", return_value=False), \
             patch.object(infrastructure.Infrastructure, "ensure_db") as db, \
             patch.object(infrastructure.Infrastructure, "ensure_ollama") as oll:
            assert infrastructure.bring_up() is False
            db.assert_not_called()
            oll.assert_not_called()

    def test_returns_false_if_db_fails(self):
        with patch.object(infrastructure.Infrastructure, "ensure_docker", return_value=True), \
             patch.object(infrastructure.Infrastructure, "ensure_db", return_value=False), \
             patch.object(infrastructure.Infrastructure, "ensure_ollama") as oll:
            assert infrastructure.bring_up() is False
            oll.assert_not_called()

    def test_returns_true_on_full_success(self):
        with patch.object(infrastructure.Infrastructure, "ensure_docker", return_value=True), \
             patch.object(infrastructure.Infrastructure, "ensure_db", return_value=True), \
             patch.object(infrastructure.Infrastructure, "ensure_ollama", return_value=True):
            assert infrastructure.bring_up() is True
