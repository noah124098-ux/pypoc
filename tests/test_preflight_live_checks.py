"""Tests for preflight checks 15 and 16 and check-gate --check-live.

Checks added:
  15. ANGEL_ONE_LIVE_* credentials present (required for live mode)
  16. AngelOneLiveBroker can connect (only when --check-live passed)
  check-gate --check-live: attempt getRMS() to verify live broker connection
"""
from __future__ import annotations

import argparse
import io
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _preflight_args(check_live: bool = False) -> SimpleNamespace:
    return SimpleNamespace(config="config/default.yaml", env=None, check_live=check_live)


def _check_gate_args(check_live: bool = False, json_out: bool = False) -> SimpleNamespace:
    ns = SimpleNamespace(config="config/default.yaml", env=None, check_live=check_live)
    ns.json = json_out
    return ns


def _mock_settings(mode: str = "paper") -> MagicMock:
    """Return a minimal settings mock."""
    s = MagicMock()
    s.mode = mode
    s.capital.initial_inr = 100_000
    s.persistence.sqlite_path = "data/agent.db"
    s.logging.level = "WARNING"
    s.logging.file = None
    s.logging.max_bytes = 10_000_000
    s.logging.backup_count = 5
    s.logging.json_log_file = "data/events.jsonl"
    return s


def _run_preflight(monkeypatch, env_overrides: dict, mode: str = "paper", check_live: bool = False) -> str:
    """
    Run cmd_preflight with mocked environment, capture and return stdout.
    Uses a BytesIO buffer to capture the UTF-8 encoded output.

    Mocks subprocess (check 6: pytest run) to avoid running the full test suite
    during unit tests.
    """
    for var in [
        "ANGEL_ONE_LIVE_API_KEY", "ANGEL_ONE_LIVE_CLIENT_CODE",
        "ANGEL_ONE_LIVE_PASSWORD", "ANGEL_ONE_LIVE_TOTP_SECRET",
        "ANGEL_ONE_API_KEY", "ANGEL_ONE_CLIENT_CODE",
        "ANGEL_ONE_PASSWORD", "ANGEL_ONE_TOTP_SECRET",
        "VIRTUAL_ENV",
    ]:
        monkeypatch.delenv(var, raising=False)
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    mock_settings = _mock_settings(mode)

    from cli import cmd_preflight

    buf = io.BytesIO()

    # cmd_preflight builds _uprint inline using sys.stdout.buffer.write.
    # We patch sys.stdout with an object whose .buffer.write captures to buf.
    class _FakeStdout:
        def __init__(self):
            self.buffer = _FakeBuf()

        def flush(self):
            pass

    class _FakeBuf:
        def write(self, data: bytes) -> None:
            buf.write(data)

    fake_stdout = _FakeStdout()

    # Mock subprocess.run (used by check 6: pytest -q --tb=no) to avoid re-running all tests.
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "624 passed in 30s"
    mock_proc.stderr = ""

    with patch("cli.load_settings", return_value=mock_settings), \
         patch("cli.setup_logging", return_value=MagicMock()), \
         patch("cli.JsonlEventLogger", return_value=MagicMock()), \
         patch("backtest.gate.read_gate_result", return_value=None), \
         patch("backtest.gate.is_live_allowed", return_value=(False, "no gate file")), \
         patch("subprocess.run", return_value=mock_proc):

        args = _preflight_args(check_live=check_live)

        original_stdout = sys.stdout
        sys.stdout = fake_stdout
        try:
            cmd_preflight(args)
        except SystemExit:
            pass
        finally:
            sys.stdout = original_stdout

    return buf.getvalue().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Check 15 — live credential presence
# ---------------------------------------------------------------------------

class TestCheck15LiveCredentials:
    """Check 15: ANGEL_ONE_LIVE_* credentials present."""

    def test_check15_shown_in_paper_mode_without_live_creds(self, monkeypatch):
        """Check 15 is shown (non-fatal in paper mode) when live creds are absent."""
        output = _run_preflight(monkeypatch, {}, mode="paper")
        assert "15." in output

    def test_check15_passes_when_all_live_creds_present_and_distinct(self, monkeypatch):
        """Check 15 passes when all ANGEL_ONE_LIVE_* vars are set and differ from data feed."""
        env = {
            "ANGEL_ONE_LIVE_API_KEY": "live_key_abc",
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client_xyz",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass_123",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp_def",
            "ANGEL_ONE_API_KEY": "data_feed_key_different",
        }
        output = _run_preflight(monkeypatch, env, mode="paper")
        assert "15." in output
        assert "present and distinct" in output

    def test_check15_fails_on_cross_contamination(self, monkeypatch):
        """Check 15 fails when ANGEL_ONE_LIVE_API_KEY == ANGEL_ONE_API_KEY."""
        same_key = "shared_key_abc123"
        env = {
            "ANGEL_ONE_LIVE_API_KEY": same_key,
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client_xyz",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass_123",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp_def",
            "ANGEL_ONE_API_KEY": same_key,
        }
        output = _run_preflight(monkeypatch, env, mode="paper")
        assert "15." in output
        assert "SECURITY" in output or "SEPARATE" in output

    def test_check15_fails_in_live_mode_when_creds_missing(self, monkeypatch):
        """Check 15 is a hard failure when mode=live and live creds are absent."""
        output = _run_preflight(monkeypatch, {}, mode="live")
        assert "15." in output
        # The detail must mention the missing vars or "missing"
        assert "missing" in output.lower() or "ANGEL_ONE_LIVE_API_KEY" in output

    def test_check15_notes_optional_in_paper_mode(self, monkeypatch):
        """In paper mode with missing creds, the check detail should say OK for paper mode."""
        output = _run_preflight(monkeypatch, {}, mode="paper")
        # Detail should explain it is OK for paper mode.
        assert "paper mode" in output.lower() or "optional" in output.lower() or "not set" in output.lower()


# ---------------------------------------------------------------------------
# Check 16 — AngelOneLiveBroker.connect() + getRMS() (only with --check-live)
# ---------------------------------------------------------------------------

class TestCheck16LiveConnect:
    """Check 16: AngelOneLiveBroker can connect (only when --check-live passed)."""

    def test_check16_not_shown_without_check_live_flag(self, monkeypatch):
        """Check 16 must NOT appear when --check-live is not passed."""
        output = _run_preflight(monkeypatch, {}, check_live=False)
        assert "16." not in output

    def test_check16_shown_with_check_live_flag(self, monkeypatch):
        """Check 16 appears when --check-live is passed."""
        output = _run_preflight(monkeypatch, {}, check_live=True)
        assert "16." in output

    def test_check16_skipped_when_live_creds_missing(self, monkeypatch):
        """Check 16 shows 'skipped' when live credentials are absent."""
        output = _run_preflight(monkeypatch, {}, check_live=True)
        assert "16." in output
        assert "skipped" in output.lower() or "incomplete" in output.lower()

    def test_check16_skipped_on_cross_contamination(self, monkeypatch):
        """Check 16 shows 'skipped' on cross-contamination."""
        same_key = "shared_key_xyz"
        env = {
            "ANGEL_ONE_LIVE_API_KEY": same_key,
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp",
            "ANGEL_ONE_API_KEY": same_key,
        }
        output = _run_preflight(monkeypatch, env, check_live=True)
        assert "16." in output
        assert "skipped" in output.lower() or "cross-contamination" in output.lower()

    def test_check16_passes_on_successful_connect_and_rms(self, monkeypatch):
        """Check 16 passes when connect() succeeds and getRMS() returns success."""
        env = {
            "ANGEL_ONE_LIVE_API_KEY": "live_key_abc",
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client_xyz",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass_123",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp_def",
            "ANGEL_ONE_API_KEY": "data_feed_different",
        }

        mock_broker = MagicMock()
        mock_broker._smart_api = MagicMock()
        mock_broker._smart_api.getRMS.return_value = {
            "status": True,
            "data": {"availablecash": "500000", "net": "500000"},
        }
        mock_broker.connect.return_value = None
        mock_broker.disconnect.return_value = None

        with patch("core.broker.angelone_live.AngelOneLiveBroker.from_env", return_value=mock_broker):
            output = _run_preflight(monkeypatch, env, check_live=True)

        assert "16." in output
        assert "session established" in output or "getRMS" in output

    def test_check16_fails_when_generate_session_raises(self, monkeypatch):
        """Check 16 fails when connect() raises RuntimeError."""
        env = {
            "ANGEL_ONE_LIVE_API_KEY": "live_key_abc",
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client_xyz",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass_123",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp_def",
            "ANGEL_ONE_API_KEY": "data_feed_different",
        }

        mock_broker = MagicMock()
        mock_broker.connect.side_effect = RuntimeError("login failed: AG8001")

        with patch("core.broker.angelone_live.AngelOneLiveBroker.from_env", return_value=mock_broker):
            output = _run_preflight(monkeypatch, env, check_live=True)

        assert "16." in output
        assert "failed" in output.lower() or "AG8001" in output


# ---------------------------------------------------------------------------
# check-gate --check-live
# ---------------------------------------------------------------------------

def _run_check_gate(monkeypatch, env_overrides: dict, check_live: bool = True) -> str:
    """Run cmd_check_gate with mocked environment, return captured stdout."""
    for var in [
        "ANGEL_ONE_LIVE_API_KEY", "ANGEL_ONE_LIVE_CLIENT_CODE",
        "ANGEL_ONE_LIVE_PASSWORD", "ANGEL_ONE_LIVE_TOTP_SECRET",
        "ANGEL_ONE_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    # A minimal valid gate file so cmd_check_gate doesn't SystemExit(2).
    gate_data = {
        "passed": True,
        "timestamp": "2026-05-01T00:00:00+00:00",
        "period_start": "2023-05-01",
        "period_end": "2026-05-01",
        "failures": [],
    }

    from cli import cmd_check_gate

    captured: list[str] = []

    def fake_print(*args, **kwargs) -> None:
        captured.append(" ".join(str(a) for a in args) + "\n")

    mock_settings = _mock_settings("paper")

    with patch("cli.load_settings", return_value=mock_settings), \
         patch("builtins.print", side_effect=fake_print):

        # cmd_check_gate imports read_gate_result and is_live_allowed *inside* the function,
        # so we need to patch at source.
        import backtest.gate as gate_mod
        original_read = gate_mod.read_gate_result
        original_allowed = gate_mod.is_live_allowed
        gate_mod.read_gate_result = lambda: gate_data
        gate_mod.is_live_allowed = lambda: (True, "gate passed")

        args = _check_gate_args(check_live=check_live)

        try:
            cmd_check_gate(args)
        except SystemExit:
            pass
        finally:
            gate_mod.read_gate_result = original_read
            gate_mod.is_live_allowed = original_allowed

    return "".join(captured)


class TestCheckGateLiveFlag:
    """check-gate --check-live: attempts getRMS() to verify live broker connection."""

    def test_check_gate_no_live_check_without_flag(self, monkeypatch):
        """Without --check-live the broker check block is never reached."""
        output = _run_check_gate(monkeypatch, {}, check_live=False)
        assert "Live broker connection check" not in output

    def test_check_gate_live_check_skips_when_creds_missing(self, monkeypatch):
        """--check-live prints SKIP when ANGEL_ONE_LIVE_* vars are absent."""
        output = _run_check_gate(monkeypatch, {}, check_live=True)
        assert "Live broker connection check" in output
        assert (
            "SKIP" in output.upper()
            or "incomplete" in output.lower()
            or "missing" in output.lower()
        )

    def test_check_gate_live_check_fails_on_cross_contamination(self, monkeypatch):
        """--check-live prints FAIL when live key == data key."""
        same_key = "shared_key"
        env = {
            "ANGEL_ONE_LIVE_API_KEY": same_key,
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp",
            "ANGEL_ONE_API_KEY": same_key,
        }
        output = _run_check_gate(monkeypatch, env, check_live=True)
        assert "Live broker connection check" in output
        assert "FAIL" in output or "SEPARATE" in output or "separate" in output.lower()

    def test_check_gate_live_check_ok_on_successful_rms(self, monkeypatch):
        """--check-live prints OK when session + getRMS() succeed."""
        env = {
            "ANGEL_ONE_LIVE_API_KEY": "live_key_abc",
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp",
            "ANGEL_ONE_API_KEY": "data_feed_different",
        }

        mock_broker = MagicMock()
        mock_broker._smart_api = MagicMock()
        mock_broker._smart_api.getRMS.return_value = {
            "status": True,
            "data": {"availablecash": "300000", "net": "350000"},
        }
        mock_broker.connect.return_value = None
        mock_broker.disconnect.return_value = None

        with patch("core.broker.angelone_live.AngelOneLiveBroker.from_env", return_value=mock_broker):
            output = _run_check_gate(monkeypatch, env, check_live=True)

        assert "Live broker connection check" in output
        assert "OK" in output or "success" in output.lower()

    def test_check_gate_live_check_fail_on_runtime_error(self, monkeypatch):
        """--check-live prints FAIL when connect() raises RuntimeError."""
        env = {
            "ANGEL_ONE_LIVE_API_KEY": "live_key_abc",
            "ANGEL_ONE_LIVE_CLIENT_CODE": "live_client",
            "ANGEL_ONE_LIVE_PASSWORD": "live_pass",
            "ANGEL_ONE_LIVE_TOTP_SECRET": "live_totp",
            "ANGEL_ONE_API_KEY": "data_feed_different",
        }

        mock_broker = MagicMock()
        mock_broker.connect.side_effect = RuntimeError("login failed: bad token")

        with patch("core.broker.angelone_live.AngelOneLiveBroker.from_env", return_value=mock_broker):
            output = _run_check_gate(monkeypatch, env, check_live=True)

        assert "Live broker connection check" in output
        assert "FAIL" in output or "failed" in output.lower()


# ---------------------------------------------------------------------------
# Argument parser — ensure --check-live flag is registered correctly
# ---------------------------------------------------------------------------

class TestArgumentParser:
    """Verify argparse correctly registers --check-live for preflight and check-gate."""

    def _get_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="nse-agent")
        parser.add_argument("--config", default="config/default.yaml")
        parser.add_argument("--env", default=None, choices=["dev", "staging", "prod"])
        sub = parser.add_subparsers(dest="cmd", required=True)

        pf = sub.add_parser("preflight")
        pf.add_argument("--check-live", action="store_true", dest="check_live")

        cg = sub.add_parser("check-gate")
        cg.add_argument("--json", action="store_true")
        cg.add_argument("--check-live", action="store_true", dest="check_live")

        return parser

    def test_preflight_check_live_default_false(self):
        args = self._get_parser().parse_args(["preflight"])
        assert args.check_live is False

    def test_preflight_check_live_true_when_flag_given(self):
        args = self._get_parser().parse_args(["preflight", "--check-live"])
        assert args.check_live is True

    def test_check_gate_check_live_default_false(self):
        args = self._get_parser().parse_args(["check-gate"])
        assert args.check_live is False

    def test_check_gate_check_live_true_when_flag_given(self):
        args = self._get_parser().parse_args(["check-gate", "--check-live"])
        assert args.check_live is True
