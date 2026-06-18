"""Append-only audit log for privileged actions (halt/resume/orders/credentials/config).

Separate from the rotating application log so forensic evidence is never lost to
rotation. One JSON object per line. Never raises — auditing must not break the action.

Schema per line:
    {ts, action, user, ip, detail}

This is a money-touching system; before go-live we must be able to answer
"who triggered this halt/order/credential change, when, and from where".
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("agent.audit")

AUDIT_PATH = Path("logs/audit.jsonl")


def audit(action: str, *, user: str = "?", ip: Optional[str] = None, **detail: Any) -> None:
    """Append one audit event. Never raises (best-effort; logs a warning on failure)."""
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "user": user,
            "ip": ip,
            "detail": detail,
        }
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:  # pragma: no cover - best effort
        log.warning("audit log write failed for %s: %s", action, e)
