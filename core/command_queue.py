import json, uuid, logging, hmac, hashlib, os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("agent.command_queue")
QUEUE_PATH = Path("data/commands.jsonl")

ALLOWED_TYPES: frozenset = frozenset(
    ["halt_agent", "resume_agent", "update_risk_param", "place_paper_order", "reload_config"]
)


def _secret() -> str:
    """HMAC secret for command signing. Empty string disables signing (legacy mode)."""
    return os.getenv("COMMAND_QUEUE_SECRET", "").strip()


def _sign(cmd_id: str, cmd_type: str, params: dict, requested_at: str) -> str:
    """Deterministic HMAC-SHA256 over the command's identity fields."""
    secret = _secret()
    if not secret:
        return ""
    payload = json.dumps(
        {"id": cmd_id, "type": cmd_type, "params": params, "requested_at": requested_at},
        sort_keys=True, separators=(",", ":"),
    )
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


@dataclass
class Command:
    id: str
    type: str        # halt_agent | resume_agent | place_paper_order | update_risk_param
    params: dict
    requested_at: str
    status: str = "pending"   # pending | processing | done | rejected
    result: Optional[str] = None
    sig: str = ""             # HMAC-SHA256 of (id,type,params,requested_at); "" if signing off

def enqueue(cmd_type: str, params: dict) -> Command:
    if cmd_type not in ALLOWED_TYPES:
        raise ValueError(f"Unknown command type: {cmd_type!r}")
    cmd_id = str(uuid.uuid4())
    requested_at = datetime.utcnow().isoformat()
    cmd = Command(
        id=cmd_id, type=cmd_type, params=params, requested_at=requested_at,
        sig=_sign(cmd_id, cmd_type, params, requested_at),
    )
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(asdict(cmd)) + "\n")
    log.info("Queued command %s: %s", cmd.type, cmd.id)
    return cmd

def read_pending() -> list[Command]:
    if not QUEUE_PATH.exists(): return []
    secret = _secret()
    cmds = []
    with open(QUEUE_PATH) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if d.get("status") != "pending":
                    continue
                # When signing is enabled, reject any command whose HMAC doesn't verify —
                # blocks injected commands from a process that lacks the secret.
                if secret:
                    expected = _sign(d.get("id", ""), d.get("type", ""),
                                     d.get("params", {}), d.get("requested_at", ""))
                    given = d.get("sig", "")
                    if not given or not hmac.compare_digest(expected, given):
                        log.warning("Rejected command %s: bad/missing HMAC signature", d.get("id"))
                        update_status(d.get("id", ""), "rejected", "bad signature")
                        continue
                cmds.append(Command(**d))
            except Exception: pass
    return cmds

def update_status(cmd_id: str, status: str, result: str = "") -> None:
    if not QUEUE_PATH.exists(): return
    lines = QUEUE_PATH.read_text().splitlines()
    updated = []
    for line in lines:
        try:
            d = json.loads(line)
            if d.get("id") == cmd_id:
                d["status"] = status; d["result"] = result
            updated.append(json.dumps(d))
        except Exception: updated.append(line)
    QUEUE_PATH.write_text("\n".join(updated) + "\n")

def clear_done() -> None:
    if not QUEUE_PATH.exists(): return
    lines = [l for l in QUEUE_PATH.read_text().splitlines()
             if json.loads(l).get("status") not in ("done","rejected")]
    QUEUE_PATH.write_text("\n".join(lines) + ("\n" if lines else ""))
