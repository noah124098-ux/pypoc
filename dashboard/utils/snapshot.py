"""Snapshot read/write utilities for the dashboard."""
from __future__ import annotations

import json
import logging
from pathlib import Path

SNAPSHOT_PATH = Path("data/snapshot.json")
GATE_PATH = Path("data/backtest_gate.json")
CONFIG_PATH = Path("config/default.yaml")
LAST_REVIEW_PATH = Path("data/last_review.json")

logger = logging.getLogger(__name__)


def read_snapshot() -> dict:
    """Read data/snapshot.json. Returns {} on missing/error."""
    if not SNAPSHOT_PATH.exists():
        return {}
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_gate() -> dict:
    """Read data/backtest_gate.json. Returns {} on missing/error."""
    if not GATE_PATH.exists():
        return {}
    try:
        return json.loads(GATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_config() -> dict:
    """Read config/default.yaml. Returns {} on missing/error."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def read_last_review() -> dict:
    """Read data/last_review.json if it exists."""
    if not LAST_REVIEW_PATH.exists():
        return {}
    try:
        return json.loads(LAST_REVIEW_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_last_review(report_dict: dict) -> None:
    """Persist a review report dict to data/last_review.json."""
    try:
        LAST_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_REVIEW_PATH.write_text(
            json.dumps(report_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Could not save last review: %s", exc)


def write_config_risk(updates: dict) -> str:
    """Patch the risk section of config/default.yaml in-place. Returns status message."""
    try:
        import yaml
        text = CONFIG_PATH.read_text(encoding="utf-8")
        cfg = yaml.safe_load(text) or {}
        for k, v in updates.items():
            cfg.setdefault("risk", {})[k] = v
        CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        return "Config saved. Restart the agent to apply."
    except Exception as e:
        return f"Save failed: {e}"
