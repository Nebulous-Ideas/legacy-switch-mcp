"""Plan / apply / rollback for the few settings this server may change on the USRobotics 7724.

Nothing is written by a plan_* call. apply_plan() needs the plan id and confirm=True, re-reads the switch and refuses if it no
longer matches what the plan saw, saves a snapshot, writes, reads back and verifies. Every apply returns a rollback plan.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import dlinkchanges, dlinkweb, webui

STATE_DIR = Path(os.environ.get("SWITCH_MCP_STATE", Path.home() / ".local" / "state" / "switch-mcp"))
PLAN_TTL = 600.0
PLANS: dict[str, "Plan"] = {}


@dataclass
class Plan:
    id: str
    switch: str
    op: str                      # "vlan" | "port" | "mirror"
    params: dict                 # what to write
    before: dict                 # same shape, as read just now
    summary: list[str]
    risks: list[str]
    created: float = field(default_factory=time.time)
    status: str = "planned"      # planned | applied | expired

    def public(self) -> dict:
        d = asdict(self)
        d["expires_in_seconds"] = max(0, int(PLAN_TTL - (time.time() - self.created)))
        return d


def _register(plan: Plan) -> dict:
    PLANS[plan.id] = plan
    return plan.public()


def _new(switch: str, op: str, params: dict, before: dict, summary: list[str], risks: list[str]) -> Plan:
    return Plan(uuid.uuid4().hex[:10], switch, op, params, before, summary, risks)


def register_dlink(switch: str, planned: dict) -> dict:
    return _register(_new(switch, planned["op"], planned["params"], planned["before"], planned["summary"], planned["risks"]))


# ---- reachability: which up ports can talk to which (port-based VLAN: same group = can talk) ----------------------
def _links(groups: list[dict], up: set[int]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for g in groups:
        members = sorted(m for m in g["members"] if m in up)
        pairs.update((a, b) for i, a in enumerate(members) for b in members[i + 1:])
    return pairs


def _vlan_risks(before_groups: list[dict], after_groups: list[dict], ports: list[dict]) -> list[str]:
    up = {p["port"] for p in ports if p["link"] != "Down"}
    lost = sorted(_links(before_groups, up) - _links(after_groups, up))
    risks = []
    if lost:
        shown = ", ".join(f"{a:02d}<->{b:02d}" for a, b in lost[:12])
        risks.append(f"{len(lost)} pair(s) of connected ports could no longer talk to each other: {shown}{' ...' if len(lost) > 12 else ''}. "
                     "If one of them is the link to the firewall or to this machine, you can lose access to the switch's web page.")
    gained = sorted(_links(after_groups, up) - _links(before_groups, up))
    if gained:
        risks.append(f"{len(gained)} new pair(s) of connected ports could talk to each other (they were separated before).")
    return risks


def plan_vlan_group(switch: str, group: int, members: list[int], description: str | None = None) -> dict:
    with webui.Session(switch) as s:
        st = s.state()
    cur = next((g for g in st["vlan"] if g["group"] == group), None)
    if cur is None:
        raise ValueError(f"no VLAN group {group}")
    desc = cur["description"] if description is None else description
    before = {"group": group, "description": cur["description"], "members": cur["members"]}
    params = {"group": group, "description": desc, "members": sorted(set(members))}
    if params == before:
        raise ValueError("nothing to change: the group already looks like that")
    after_groups = [params if g["group"] == group else g for g in st["vlan"]]
    summary = []
    if desc != cur["description"]:
        summary.append(f"group {group:02d} description {cur['description']!r} -> {desc!r}")
    added = sorted(set(params["members"]) - set(cur["members"]))
    removed = sorted(set(cur["members"]) - set(params["members"]))
    if added:
        summary.append(f"group {group:02d}: add ports {', '.join(f'{p:02d}' for p in added)}")
    if removed:
        summary.append(f"group {group:02d}: remove ports {', '.join(f'{p:02d}' for p in removed)}")
    risks = _vlan_risks(st["vlan"], [{**g, "members": g["members"]} for g in after_groups], st["ports"])
    return _register(_new(switch, "vlan", params, before, summary, risks))


def plan_port(switch: str, port: int, speed: str, flow: bool, qos: str) -> dict:
    with webui.Session(switch) as s:
        st = s.state()
    cur = next((p for p in st["ports"] if p["port"] == port), None)
    if cur is None:
        raise ValueError(f"no port {port}")
    before = {"port": port, "speed": cur["speed_set"], "flow": cur["flow_set"] == "Enable", "qos": cur["qos"]}
    params = {"port": port, "speed": speed, "flow": flow, "qos": qos}
    if params == before:
        raise ValueError("nothing to change")
    summary = [f"port {port:02d}: {k} {before[k]!r} -> {params[k]!r}" for k in ("speed", "flow", "qos") if before[k] != params[k]]
    risks = []
    if cur["link"] != "Down":
        if speed == "Disable":
            risks.append(f"port {port:02d} is up ({cur['link']}); disabling it drops whatever is connected.")
        elif speed != "Auto":
            risks.append(f"port {port:02d} is up ({cur['link']}); a forced speed must match the device on the other end, and turns off Auto-MDIX.")
    return _register(_new(switch, "port", params, before, summary, risks))


def plan_mirror(switch: str, mode: str, sniffer_port: int | None, source_port: int | None) -> dict:
    with webui.Session(switch) as s:
        st = s.state()
    cur = st["mirror"]
    before = {"mode": cur["mode"], "sniffer_port": cur["sniffer_port"], "source_port": cur["source_port"]}
    if mode == "Disable":
        params = {"mode": "Disable", "sniffer_port": None, "source_port": None}
    else:
        params = {"mode": mode, "sniffer_port": sniffer_port, "source_port": source_port}
    if params == before:
        raise ValueError("nothing to change")
    summary = [f"mirror {before['mode']} (sniffer {before['sniffer_port']}, source {before['source_port']}) -> "
               f"{params['mode']} (sniffer {params['sniffer_port']}, source {params['source_port']})"]
    risks = []
    up = {p["port"]: p["link"] for p in st["ports"] if p["link"] != "Down"}
    if mode != "Disable" and sniffer_port in up:
        risks.append(f"sniffer port {sniffer_port:02d} is up ({up[sniffer_port]}); it becomes the monitoring port, so do not use it for normal traffic.")
    return _register(_new(switch, "mirror", params, before, summary, risks))


# ---- apply ------------------------------------------------------------------------------------------------------------
def _write(s: webui.Session, op: str, p: dict) -> None:
    if op == "vlan":
        s.write_vlan(p["group"], p["description"], p["members"])
    elif op == "port":
        s.write_port(p["port"], p["speed"], p["flow"], p["qos"])
    elif op == "mirror":
        s.write_mirror(p["mode"], p["sniffer_port"], p["source_port"])
    else:
        raise ValueError(op)


def _current(op: str, st: dict, p: dict) -> dict:
    if op == "vlan":
        g = next(g for g in st["vlan"] if g["group"] == p["group"])
        return {"group": g["group"], "description": g["description"], "members": g["members"]}
    if op == "port":
        x = next(x for x in st["ports"] if x["port"] == p["port"])
        return {"port": x["port"], "speed": x["speed_set"], "flow": x["flow_set"] == "Enable", "qos": x["qos"]}
    m = st["mirror"]
    return {"mode": m["mode"], "sniffer_port": m["sniffer_port"], "source_port": m["source_port"]}


def apply_plan(plan_id: str, confirm: bool) -> dict:
    if confirm is not True:
        raise ValueError("apply_plan needs confirm=true; show the plan to the user first")
    plan = PLANS.get(plan_id)
    if plan is None:
        raise KeyError(f"unknown plan {plan_id!r} (plans live only in this server process)")
    if plan.status != "planned":
        raise ValueError(f"plan {plan_id} is {plan.status}")
    if time.time() - plan.created > PLAN_TTL:
        plan.status = "expired"
        raise ValueError("plan expired; make a new one")
    dlink = plan.op.startswith("dl_")
    with webui.open_session(plan.switch) as s:
        st = s.state()
        now = dlinkchanges.current(plan.op, st, plan.params) if dlink else _current(plan.op, st, plan.params)
        if now != plan.before:
            raise RuntimeError(f"switch changed since the plan was made; now {now}, plan saw {plan.before}. Plan a new change.")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        snap = STATE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{plan.switch}-{plan.op}-{plan.id}.json"
        snap.write_text(json.dumps({"plan": plan.public(), "state": st}, indent=1))
        (dlinkchanges.write if dlink else _write)(s, plan.op, plan.params)
        st_after = s.state()
        after = dlinkchanges.current(plan.op, st_after, plan.params) if dlink else _current(plan.op, st_after, plan.params)
    plan.status = "applied"
    expected = dlinkchanges.target(plan.op, plan.params) if dlink else plan.params
    verified = after == expected
    if dlink:
        inv_op, inv_params = dlinkchanges.inverse(plan.op, plan.params, plan.before)
        rollback = _register(_new(plan.switch, inv_op, inv_params, expected, [f"undo plan {plan.id}"], []))
    else:
        rollback = _register(_new(plan.switch, plan.op, plan.before, plan.params, [f"undo plan {plan.id}"], []))
    return {"applied": True, "verified": verified, "before": plan.before, "after": after, "snapshot": str(snap),
            "rollback_plan_id": rollback["id"],
            **({} if verified else {"warning": "the switch did not read back as requested; check it, and use the rollback plan if needed"})}
