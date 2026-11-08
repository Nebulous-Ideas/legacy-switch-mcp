"""Plans for the D-Link DGS-1100 EasySmart: 802.1Q VLANs, per-port PVIDs, port settings and the port mirror.

The switch's own web page refuses some changes in JavaScript before it posts; the same rules are repeated here so a plan that the web
page would reject is rejected at plan time. Risks are computed against the live state (which ports are up, what VLAN 1 reaches).
"""
from __future__ import annotations

from . import dlinkweb, webui

PORTS = dlinkweb.PORTS


def _state(switch: str) -> dict:
    with webui.open_session(switch) as s:
        if not isinstance(s, dlinkweb.DLinkSession):
            raise ValueError(f"{switch!r} is not a D-Link switch")
        return s.state()


def _vlan(st: dict, vid: int) -> dict | None:
    v = next((v for v in st["vlan"]["vlans"] if v["vid"] == vid), None)
    return None if v is None else {"vid": v["vid"], "name": v["name"], "untagged": v["untagged"], "tagged": v["tagged"]}


def _pvids(st: dict) -> list[int]:
    return [p["pvid"] for p in st["ports"]]


def _mgmt_vid(st: dict) -> int:
    m = st["vlan"]["management_vlan"]
    return m["vid"] if m["enabled"] else 1


def _require_8021q(st: dict) -> None:
    if not st["vlan"]["enabled_802_1q"]:
        raise ValueError("802.1Q VLAN mode is off on this switch; this server does not switch modes")


def _risks(st: dict, vlans_after: list[dict], pvids_after: list[int]) -> list[str]:
    """What could cut you off: up ports that stop being in the management VLAN, or whose untagged VLAN changes."""
    risks, mvid = [], _mgmt_vid(st)
    up = {p["port"] for p in st["ports"] if p["link"] != "Down"}
    members = lambda vl, vid: {p for v in vl if v["vid"] == vid for p in v["untagged"] + v["tagged"]}
    lost = sorted((members(st["vlan"]["vlans"], mvid) - members(vlans_after, mvid)) & up)
    if lost:
        risks.append(f"port(s) {', '.join(f'{p:02d}' for p in lost)} are up and would leave VLAN {mvid} (the management VLAN): the switch's web page "
                     "may stop being reachable through them, including the port this server or the firewall uses.")
    moved = [(p, a, b) for p, (a, b) in enumerate(zip(_pvids(st), pvids_after), 1) if a != b and p in up]
    for p, a, b in moved:
        risks.append(f"port {p:02d} is up and its untagged traffic moves from VLAN {a} to VLAN {b}"
                     + (f" (it is no longer on the management VLAN {mvid})" if a == mvid else "") + ".")
    return risks


def _check_untag_unique(st: dict, vid: int, untagged: list[int]) -> None:
    for v in st["vlan"]["vlans"]:
        if v["vid"] != vid and set(v["untagged"]) & set(untagged):
            both = sorted(set(v["untagged"]) & set(untagged))
            raise ValueError(f"port(s) {both} are already untagged in VLAN {v['vid']}; a port can be untagged in only one VLAN "
                             "(remove it there first, or make it tagged here)")


# ---- plans (return the pieces changes.py needs) ---------------------------------------------------------------------------
def plan_vlan(switch: str, vid: int, name: str | None, untagged: list[int], tagged: list[int]) -> dict:
    st = _state(switch)
    _require_8021q(st)
    cur = _vlan(st, vid)
    new_name = (cur["name"] if cur else "") if name is None else name
    params = {"vid": vid, "name": new_name, "untagged": sorted(set(untagged)), "tagged": sorted(set(tagged))}
    if cur == params:
        raise ValueError("nothing to change")
    if not cur and len(st["vlan"]["vlans"]) >= 256:
        raise ValueError("too many VLANs")
    _check_untag_unique(st, vid, params["untagged"])
    if not params["untagged"] and not params["tagged"] and vid == 1:
        raise ValueError("VLAN 1 must keep at least one member")
    vlans_after = [params if v["vid"] == vid else v for v in st["vlan"]["vlans"]] + ([] if cur else [params])
    summary = [f"VLAN {vid}: {'create' if not cur else 'edit'} name {new_name!r}, untagged {params['untagged']}, tagged {params['tagged']}"]
    if cur:
        summary.append(f"was: name {cur['name']!r}, untagged {cur['untagged']}, tagged {cur['tagged']}")
    return {"op": "dl_vlan", "params": params, "before": cur, "summary": summary, "risks": _risks(st, vlans_after, _pvids(st))}


def plan_vlan_delete(switch: str, vid: int) -> dict:
    st = _state(switch)
    _require_8021q(st)
    cur = _vlan(st, vid)
    if cur is None:
        raise ValueError(f"no VLAN {vid}")
    if vid == 1:
        raise ValueError("VLAN 1 cannot be deleted")
    if vid == _mgmt_vid(st):
        raise ValueError("this is the management VLAN")
    if not any(v["vid"] != vid and (v["untagged"] or v["tagged"]) for v in st["vlan"]["vlans"]):
        raise ValueError("another VLAN must keep at least one member")
    using = [p["port"] for p in st["ports"] if p["pvid"] == vid]
    if using:
        raise ValueError(f"port(s) {using} still use VLAN {vid} as their PVID; change their PVID first")
    vlans_after = [v for v in st["vlan"]["vlans"] if v["vid"] != vid]
    return {"op": "dl_vlan_delete", "params": {"vid": vid}, "before": cur,
            "summary": [f"delete VLAN {vid} ({cur['name']!r}, untagged {cur['untagged']}, tagged {cur['tagged']})"],
            "risks": _risks(st, vlans_after, _pvids(st))}


def plan_pvid(switch: str, pvids: dict[int, int]) -> dict:
    st = _state(switch)
    _require_8021q(st)
    now = _pvids(st)
    after = list(now)
    for port, vid in pvids.items():
        if not 1 <= port <= PORTS:
            raise ValueError(f"no port {port}")
        after[port - 1] = vid
    # the web page's rule: a port's PVID must be a VLAN it belongs to (or 1 if it belongs to none)
    for port in pvids:
        member_of = [v["vid"] for v in st["vlan"]["vlans"] if port in v["untagged"] + v["tagged"]]
        if after[port - 1] not in member_of and not (not member_of and after[port - 1] == 1):
            raise ValueError(f"port {port} is not a member of VLAN {after[port - 1]}; add it to that VLAN first")
    if after == now:
        raise ValueError("nothing to change")
    summary = [f"port {p:02d} PVID {a} -> {b}" for p, (a, b) in enumerate(zip(now, after), 1) if a != b]
    return {"op": "dl_pvid", "params": {"pvids": after}, "before": {"pvids": now}, "summary": summary,
            "risks": _risks(st, st["vlan"]["vlans"], after)}


def plan_port(switch: str, port: int, speed: str, flow: bool) -> dict:
    st = _state(switch)
    cur = next((p for p in st["ports"] if p["port"] == port), None)
    if cur is None:
        raise ValueError(f"no port {port}")
    before = {"port": port, "speed": cur["speed_set"], "flow": cur["flow_control"]}
    params = {"port": port, "speed": speed, "flow": flow}
    if params == before:
        raise ValueError("nothing to change")
    summary = [f"port {port:02d}: {k} {before[k]!r} -> {params[k]!r}" for k in ("speed", "flow") if before[k] != params[k]]
    risks = []
    if cur["link"] != "Down":
        if speed == "Disable":
            risks.append(f"port {port:02d} is up ({cur['link']}); disabling it drops whatever is connected.")
        elif speed != "Auto":
            risks.append(f"port {port:02d} is up ({cur['link']}); a forced speed must match the device on the other end.")
    return {"op": "dl_port", "params": params, "before": before, "summary": summary, "risks": risks}


def plan_mirror(switch: str, mode: str, sniffer_port: int | None, source_ports: list[int]) -> dict:
    st = _state(switch)
    m = st["mirror"]
    before = {"mode": m["mode"], "sniffer_port": m["sniffer_port"], "source_ports": m["source_ports"]}
    params = ({"mode": "Disable", "sniffer_port": None, "source_ports": []} if mode == "Disable"
              else {"mode": mode, "sniffer_port": sniffer_port, "source_ports": sorted(set(source_ports))})
    if mode != "Disable":
        if sniffer_port is None or not params["source_ports"] or sniffer_port in params["source_ports"]:
            raise ValueError("mirroring needs a sniffer port and at least one different source port")
    if params == before:
        raise ValueError("nothing to change")
    risks = []
    up = {p["port"]: p["link"] for p in st["ports"] if p["link"] != "Down"}
    if mode != "Disable" and sniffer_port in up:
        risks.append(f"sniffer port {sniffer_port:02d} is up ({up[sniffer_port]}); it becomes the monitoring port, so do not use it for normal traffic.")
    return {"op": "dl_mirror", "params": params, "before": before,
            "summary": [f"mirror {before['mode']} (sniffer {before['sniffer_port']}, sources {before['source_ports']}) -> "
                        f"{params['mode']} (sniffer {params['sniffer_port']}, sources {params['source_ports']})"], "risks": risks}


# ---- apply helpers used by changes.apply_plan -------------------------------------------------------------------------
def current(op: str, st: dict, params: dict):
    if op in ("dl_vlan", "dl_vlan_delete"):
        return _vlan(st, params["vid"])
    if op == "dl_pvid":
        return {"pvids": _pvids(st)}
    if op == "dl_port":
        p = next(p for p in st["ports"] if p["port"] == params["port"])
        return {"port": p["port"], "speed": p["speed_set"], "flow": p["flow_control"]}
    m = st["mirror"]
    return {"mode": m["mode"], "sniffer_port": m["sniffer_port"], "source_ports": m["source_ports"]}


def target(op: str, params: dict):
    """What current() should return once the plan has been applied."""
    return None if op == "dl_vlan_delete" else params


def write(s: dlinkweb.DLinkSession, op: str, p: dict) -> None:
    if op == "dl_vlan":
        s.write_vlan(p["vid"], p["name"], p["untagged"], p["tagged"])
    elif op == "dl_vlan_delete":
        s.delete_vlan(p["vid"])
    elif op == "dl_pvid":
        s.write_pvids(p["pvids"])
    elif op == "dl_port":
        s.write_port(p["port"], p["speed"], p["flow"])
    elif op == "dl_mirror":
        s.write_mirror(p["mode"], p["sniffer_port"], p["source_ports"])
    else:
        raise ValueError(op)


def inverse(op: str, params: dict, before):
    """(op, params) that undoes a plan, given the state it replaced."""
    if op == "dl_vlan":
        return ("dl_vlan_delete", {"vid": params["vid"]}) if before is None else ("dl_vlan", before)
    if op == "dl_vlan_delete":
        return "dl_vlan", before
    return op, before
