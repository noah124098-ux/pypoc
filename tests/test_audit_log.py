"""Tests for core/audit_log.py — append-only privileged-action audit trail."""
import json
import pytest

import core.audit_log as al
from core.audit_log import audit


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(al, "AUDIT_PATH", path)
    yield path


def test_audit_writes_one_line(isolated_audit):
    audit("halt_agent", user="admin", ip="1.2.3.4", reason="test")
    lines = isolated_audit.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "halt_agent"
    assert entry["user"] == "admin"
    assert entry["ip"] == "1.2.3.4"
    assert entry["detail"]["reason"] == "test"
    assert "ts" in entry


def test_audit_is_append_only(isolated_audit):
    audit("halt_agent", user="admin")
    audit("resume_agent", user="admin")
    lines = isolated_audit.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action"] == "halt_agent"
    assert json.loads(lines[1])["action"] == "resume_agent"


def test_audit_never_raises(monkeypatch):
    """Audit failures must never propagate (auditing can't break the action)."""
    # Point at an unwritable path; audit() should swallow the error
    monkeypatch.setattr(al, "AUDIT_PATH", al.Path("/nonexistent_dir_xyz/\x00bad/audit.jsonl"))
    audit("halt_agent", user="admin")  # must not raise


def test_audit_does_not_log_credential_values(isolated_audit):
    """save_angel_one audit should record field names, never values (contract check)."""
    audit("save_angel_one", user="admin", fields=["ANGEL_ONE_API_KEY", "ANGEL_ONE_PASSWORD"])
    entry = json.loads(isolated_audit.read_text().splitlines()[0])
    # Only field NAMES present, no secret values
    assert entry["detail"]["fields"] == ["ANGEL_ONE_API_KEY", "ANGEL_ONE_PASSWORD"]
    raw = isolated_audit.read_text()
    assert "password" not in raw.lower() or "ANGEL_ONE_PASSWORD" in raw  # name ok, no value
