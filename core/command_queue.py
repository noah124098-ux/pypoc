import json, uuid, logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("agent.command_queue")
QUEUE_PATH = Path("data/commands.jsonl")

ALLOWED_TYPES: frozenset = frozenset(
    ["halt_agent", "resume_agent", "update_risk_param", "place_paper_order", "reload_config"]
)

@dataclass
class Command:
    id: str
    type: str        # halt_agent | resume_agent | place_paper_order | update_risk_param
    params: dict
    requested_at: str
    status: str = "pending"   # pending | processing | done | rejected
    result: Optional[str] = None

def enqueue(cmd_type: str, params: dict) -> Command:
    if cmd_type not in ALLOWED_TYPES:
        raise ValueError(f"Unknown command type: {cmd_type!r}")
    cmd = Command(id=str(uuid.uuid4()), type=cmd_type, params=params,
                  requested_at=datetime.utcnow().isoformat())
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "a") as f:
        f.write(json.dumps(asdict(cmd)) + "\n")
    log.info("Queued command %s: %s", cmd.type, cmd.id)
    return cmd

def read_pending() -> list[Command]:
    if not QUEUE_PATH.exists(): return []
    cmds = []
    with open(QUEUE_PATH) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if d.get("status") == "pending":
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
