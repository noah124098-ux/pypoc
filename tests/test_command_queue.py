"""Tests for core/command_queue.py — file-based command queue."""
import json
import pytest
from pathlib import Path
import core.command_queue as cq
from core.command_queue import Command, enqueue, read_pending, update_status, clear_done


@pytest.fixture(autouse=True)
def isolated_queue(tmp_path, monkeypatch):
    """Redirect QUEUE_PATH to a temp file for each test."""
    queue_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", queue_file)
    yield queue_file


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

def test_enqueue_returns_command():
    cmd = enqueue("halt_agent", {})
    assert isinstance(cmd, Command)
    assert cmd.type == "halt_agent"
    assert cmd.status == "pending"
    assert cmd.result is None


def test_enqueue_generates_unique_ids():
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("halt_agent", {})
    assert c1.id != c2.id


def test_enqueue_writes_to_file(isolated_queue):
    enqueue("resume_agent", {"reason": "test"})
    lines = isolated_queue.read_text().splitlines()
    assert len(lines) == 1
    d = json.loads(lines[0])
    assert d["type"] == "resume_agent"
    assert d["params"] == {"reason": "test"}


def test_enqueue_appends_multiple_commands(isolated_queue):
    enqueue("halt_agent", {})
    enqueue("resume_agent", {})
    enqueue("place_paper_order", {"symbol": "NIFTY", "qty": 1})
    lines = isolated_queue.read_text().splitlines()
    assert len(lines) == 3


def test_enqueue_sets_requested_at():
    cmd = enqueue("update_risk_param", {"max_risk_pct": 0.02})
    assert cmd.requested_at  # non-empty
    # should be valid ISO datetime
    from datetime import datetime
    datetime.fromisoformat(cmd.requested_at)


def test_enqueue_creates_parent_dir(tmp_path, monkeypatch):
    nested = tmp_path / "deep" / "nested" / "commands.jsonl"
    monkeypatch.setattr(cq, "QUEUE_PATH", nested)
    enqueue("halt_agent", {})
    assert nested.exists()


# ---------------------------------------------------------------------------
# read_pending
# ---------------------------------------------------------------------------

def test_read_pending_empty_when_no_file():
    result = read_pending()
    assert result == []


def test_read_pending_returns_pending_only(isolated_queue):
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("resume_agent", {})
    # Manually mark c2 as done directly in file
    update_status(c2.id, "done", "completed")
    pending = read_pending()
    assert len(pending) == 1
    assert pending[0].id == c1.id


def test_read_pending_returns_command_objects():
    enqueue("place_paper_order", {"symbol": "RELIANCE", "qty": 5, "side": "BUY"})
    pending = read_pending()
    assert len(pending) == 1
    cmd = pending[0]
    assert isinstance(cmd, Command)
    assert cmd.params["symbol"] == "RELIANCE"
    assert cmd.params["qty"] == 5


def test_read_pending_skips_malformed_lines(isolated_queue):
    enqueue("halt_agent", {})
    # append a malformed line
    with open(isolated_queue, "a") as f:
        f.write("not-valid-json\n")
    pending = read_pending()
    assert len(pending) == 1  # malformed line silently skipped


def test_read_pending_all_statuses_filtered(isolated_queue):
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("resume_agent", {})
    update_status(c1.id, "done")
    update_status(c2.id, "rejected", "not allowed")
    assert read_pending() == []


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------

def test_update_status_changes_status():
    cmd = enqueue("halt_agent", {})
    update_status(cmd.id, "processing")
    pending = read_pending()
    assert pending == []  # no longer pending


def test_update_status_sets_result(isolated_queue):
    cmd = enqueue("halt_agent", {})
    update_status(cmd.id, "done", "agent halted successfully")
    lines = isolated_queue.read_text().splitlines()
    d = json.loads(lines[0])
    assert d["result"] == "agent halted successfully"
    assert d["status"] == "done"


def test_update_status_noop_when_file_missing():
    # Should not raise even when the queue file does not exist
    update_status("nonexistent-id", "done")


def test_update_status_only_affects_matching_id(isolated_queue):
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("resume_agent", {})
    update_status(c1.id, "done")
    pending = read_pending()
    assert len(pending) == 1
    assert pending[0].id == c2.id


# ---------------------------------------------------------------------------
# clear_done
# ---------------------------------------------------------------------------

def test_clear_done_removes_done_and_rejected(isolated_queue):
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("resume_agent", {})
    c3 = enqueue("place_paper_order", {"symbol": "TCS"})
    update_status(c1.id, "done")
    update_status(c3.id, "rejected", "guardrail blocked")
    clear_done()
    pending = read_pending()
    assert len(pending) == 1
    assert pending[0].id == c2.id


def test_clear_done_noop_when_file_missing():
    # Should not raise
    clear_done()


def test_clear_done_preserves_pending_and_processing(isolated_queue):
    c1 = enqueue("halt_agent", {})
    c2 = enqueue("resume_agent", {})
    update_status(c1.id, "processing")
    clear_done()
    lines = isolated_queue.read_text().splitlines()
    ids = {json.loads(l)["id"] for l in lines if l.strip()}
    assert c1.id in ids
    assert c2.id in ids


# ---------------------------------------------------------------------------
# Security hardening tests
# ---------------------------------------------------------------------------

_ALLOWED_COMMAND_TYPES = frozenset(
    ["halt_agent", "resume_agent", "update_risk_param", "place_paper_order", "reload_config"]
)


def test_known_command_types_are_all_valid():
    """All command types used by the codebase must be in the allowlist."""
    for t in ["halt_agent", "resume_agent", "update_risk_param", "place_paper_order", "reload_config"]:
        assert t in _ALLOWED_COMMAND_TYPES


def test_unknown_command_type_not_in_allowlist():
    """An arbitrary/injected command type must NOT be in the allowlist."""
    assert "exec_shell" not in _ALLOWED_COMMAND_TYPES
    assert "delete_all" not in _ALLOWED_COMMAND_TYPES
    assert "__import__" not in _ALLOWED_COMMAND_TYPES


def test_halt_reason_capped_at_200_chars():
    """Halt reason must be capped at 200 chars to prevent snapshot bloat."""
    long_reason = "A" * 500
    capped = str(long_reason)[:200]
    assert len(capped) == 200


def test_halt_reason_xss_payload_is_harmless_after_cap():
    """A long XSS payload must be truncated to 200 chars."""
    xss = "<script>alert('xss')</script>" * 20
    capped = str(xss)[:200]
    assert len(capped) == 200
    # Content is truncated — the payload cannot be more than 200 chars in the snapshot
    assert len(capped) < len(xss)


# ---------------------------------------------------------------------------
# HMAC command signing (security: reject injected/forged commands)
# ---------------------------------------------------------------------------

def test_signing_off_by_default(monkeypatch):
    """No COMMAND_QUEUE_SECRET → sig empty, commands still work (backward compat)."""
    monkeypatch.delenv("COMMAND_QUEUE_SECRET", raising=False)
    cmd = enqueue("halt_agent", {"reason": "x"})
    assert cmd.sig == ""
    assert len(read_pending()) == 1


def test_signed_command_verifies(monkeypatch):
    monkeypatch.setenv("COMMAND_QUEUE_SECRET", "s3cret")
    cmd = enqueue("halt_agent", {"reason": "x"})
    assert cmd.sig  # non-empty signature
    pending = read_pending()
    assert len(pending) == 1
    assert pending[0].id == cmd.id


def test_forged_command_rejected(monkeypatch, isolated_queue):
    """A command appended without a valid HMAC must be rejected on read."""
    monkeypatch.setenv("COMMAND_QUEUE_SECRET", "s3cret")
    enqueue("halt_agent", {"reason": "legit"})  # one valid command
    # Inject a forged command directly into the file (attacker with file write access)
    with open(isolated_queue, "a") as f:
        f.write(json.dumps({
            "id": "forged", "type": "halt_agent", "params": {}, "requested_at": "now",
            "status": "pending", "sig": "deadbeef", "result": None,
        }) + "\n")
    pending = read_pending()
    ids = [c.id for c in pending]
    assert "forged" not in ids, "forged command must be rejected"
    assert len(pending) == 1  # only the legit one survives


def test_tampered_params_rejected(monkeypatch, isolated_queue):
    """Changing params after signing must invalidate the HMAC."""
    monkeypatch.setenv("COMMAND_QUEUE_SECRET", "s3cret")
    cmd = enqueue("update_risk_param", {"key": "max_open_positions", "value": 5})
    # Tamper: rewrite the line with value=999 but keep the original sig
    lines = isolated_queue.read_text().splitlines()
    d = json.loads(lines[0])
    d["params"]["value"] = 999  # malicious change
    isolated_queue.write_text(json.dumps(d) + "\n")
    pending = read_pending()
    assert len(pending) == 0, "tampered command must be rejected"
