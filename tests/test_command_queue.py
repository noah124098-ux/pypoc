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
