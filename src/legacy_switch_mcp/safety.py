"""Shared safety helpers: where state lives, the audit log, and the protected-port guard."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import webui

STATE_DIR = Path(os.environ.get("SWITCH_MCP_STATE", Path.home() / ".local" / "state" / "switch-mcp"))


def audit(event: str, **fields) -> None:
    """Append one JSON line to audit.jsonl. Plans carry no secrets, so neither does the log."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "audit.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}) + "\n")
    except OSError:
        pass  # never let logging break a change


def guard_protected(switch: str, touched: set[int], allow: bool) -> None:
    """Refuse changes to ports listed as protected for this switch (uplinks, the port the firewall uses) unless asked explicitly."""
    hit = sorted(set(touched) & webui.protected_ports(switch))
    if hit and not allow:
        audit("refused", switch=switch, reason="protected ports", ports=hit)
        raise ValueError(f"port(s) {hit} are protected on {switch!r} (protected_ports in switches.json). If this is deliberate, plan again "
                         "with allow_protected=true.")
