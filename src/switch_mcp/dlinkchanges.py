"""Plans for the D-Link DGS-1100 EasySmart: 802.1Q VLANs, per-port PVIDs, port settings and the port mirror.

The switch's own web page refuses some changes in JavaScript before it posts; the same rules are repeated here so a plan that the web
page would reject is rejected at plan time. Risks are computed against the live state (which ports are up, what VLAN 1 reaches).
"""
from __future__ import annotations

import re

from . import dlinkweb, webui
from .safety import guard_protected

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
def plan_vlan(switch: str, vid: int, name: str | None, untagged: list[int], tagged: list[int], allow_protected: bool = False) -> dict:
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
    old = {p: "U" for p in (cur or {}).get("untagged", [])} | {p: "T" for p in (cur or {}).get("tagged", [])}
    new = {p: "U" for p in params["untagged"]} | {p: "T" for p in params["tagged"]}
    guard_protected(switch, {p for p in set(old) | set(new) if old.get(p) != new.get(p)}, allow_protected)
    if not params["untagged"] and not params["tagged"] and vid == 1:
        raise ValueError("VLAN 1 must keep at least one member")
    vlans_after = [params if v["vid"] == vid else v for v in st["vlan"]["vlans"]] + ([] if cur else [params])
    summary = [f"VLAN {vid}: {'create' if not cur else 'edit'} name {new_name!r}, untagged {params['untagged']}, tagged {params['tagged']}"]
    if cur:
        summary.append(f"was: name {cur['name']!r}, untagged {cur['untagged']}, tagged {cur['tagged']}")
    return {"op": "dl_vlan", "params": params, "before": cur, "summary": summary, "risks": _risks(st, vlans_after, _pvids(st))}


def plan_vlan_delete(switch: str, vid: int, allow_protected: bool = False) -> dict:
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
    guard_protected(switch, set(cur["untagged"]) | set(cur["tagged"]), allow_protected)
    vlans_after = [v for v in st["vlan"]["vlans"] if v["vid"] != vid]
    return {"op": "dl_vlan_delete", "params": {"vid": vid}, "before": cur,
            "summary": [f"delete VLAN {vid} ({cur['name']!r}, untagged {cur['untagged']}, tagged {cur['tagged']})"],
            "risks": _risks(st, vlans_after, _pvids(st))}


def plan_pvid(switch: str, pvids: dict[int, int], allow_protected: bool = False) -> dict:
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
    guard_protected(switch, {p for p, (a, b) in enumerate(zip(now, after), 1) if a != b}, allow_protected)
    summary = [f"port {p:02d} PVID {a} -> {b}" for p, (a, b) in enumerate(zip(now, after), 1) if a != b]
    return {"op": "dl_pvid", "params": {"pvids": after}, "before": {"pvids": now}, "summary": summary,
            "risks": _risks(st, st["vlan"]["vlans"], after)}


def plan_port(switch: str, port: int, speed: str, flow: bool, allow_protected: bool = False) -> dict:
    st = _state(switch)
    cur = next((p for p in st["ports"] if p["port"] == port), None)
    if cur is None:
        raise ValueError(f"no port {port}")
    before = {"port": port, "speed": cur["speed_set"], "flow": cur["flow_control"]}
    params = {"port": port, "speed": speed, "flow": flow}
    if params == before:
        raise ValueError("nothing to change")
    guard_protected(switch, {port}, allow_protected)
    summary = [f"port {port:02d}: {k} {before[k]!r} -> {params[k]!r}" for k in ("speed", "flow") if before[k] != params[k]]
    risks = []
    if cur["link"] != "Down":
        if speed == "Disable":
            risks.append(f"port {port:02d} is up ({cur['link']}); disabling it drops whatever is connected.")
        elif speed != "Auto":
            risks.append(f"port {port:02d} is up ({cur['link']}); a forced speed must match the device on the other end.")
    return {"op": "dl_port", "params": params, "before": before, "summary": summary, "risks": risks}


def plan_mirror(switch: str, mode: str, sniffer_port: int | None, source_ports: list[int], allow_protected: bool = False) -> dict:
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
    guard_protected(switch, {p for p in [params["sniffer_port"], before["sniffer_port"], *params["source_ports"], *before["source_ports"]] if p}, allow_protected)
    risks = []
    up = {p["port"]: p["link"] for p in st["ports"] if p["link"] != "Down"}
    if mode != "Disable" and sniffer_port in up:
        risks.append(f"sniffer port {sniffer_port:02d} is up ({up[sniffer_port]}); it becomes the monitoring port, so do not use it for normal traffic.")
    return {"op": "dl_mirror", "params": params, "before": before,
            "summary": [f"mirror {before['mode']} (sniffer {before['sniffer_port']}, sources {before['source_ports']}) -> "
                        f"{params['mode']} (sniffer {params['sniffer_port']}, sources {params['source_ports']})"], "risks": risks}


def plan_system(switch: str, name: str | None, location: str | None, timeout: int | None) -> dict:
    st = _state(switch)
    cur = st["system"]
    before = {"name": cur["name"], "location": cur["location"], "timeout": cur["login_timeout_minutes"]}
    params = {"name": before["name"] if name is None else name, "location": before["location"] if location is None else location,
              "timeout": before["timeout"] if timeout is None else timeout}
    if params == before:
        raise ValueError("nothing to change")
    for label in ("name", "location"):
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{0,20}", params[label]):
            raise ValueError(f"system {label}: up to 20 letters, digits, space, '.', '-' or '_'")
    if not dlinkweb.TIMEOUT_MIN <= params["timeout"] <= dlinkweb.TIMEOUT_MAX:
        raise ValueError(f"login timeout must be {dlinkweb.TIMEOUT_MIN}-{dlinkweb.TIMEOUT_MAX} minutes")
    return {"op": "dl_system", "params": params, "before": before,
            "summary": [f"system {k}: {before[k]!r} -> {params[k]!r}" for k in params if params[k] != before[k]], "risks": []}


def plan_save(switch: str) -> dict:
    _state(switch)  # proves we can log in; there is nothing to compare for a save
    return {"op": "dl_save", "params": {}, "before": {"saved": "unknown"},
            "summary": ["copy the running configuration to flash (survives a power cut)"],
            "risks": ["this saves EVERYTHING currently running, including any change you have not meant to keep; review get_state first."]}


def _tvals(switch: str) -> dict:
    with webui.open_session(switch) as s:
        return s.read("trunk")["values"]


def plan_trunk(switch: str, group: int, ports: list[int], allow_protected: bool = False) -> dict:
    """Repeats the checks of the switch's Trunking page (same speed, same VLAN setup, no mirror/loop-detection/static-MAC conflicts)."""
    st, v = _state(switch), _tvals(switch)
    if not 1 <= group <= dlinkweb.TRUNK_GROUPS:
        raise ValueError(f"trunk group must be 1-{dlinkweb.TRUNK_GROUPS}")
    ports = sorted(set(ports))
    quad = list(range(4 * group - 3, 4 * group + 1))
    if not 2 <= len(ports) <= dlinkweb.TRUNK_MAX_PORTS or any(p not in quad for p in ports):
        raise ValueError(f"trunk group {group} takes 2-{dlinkweb.TRUNK_MAX_PORTS} ports from {quad}")
    idx = [p - 1 for p in ports]
    other = {p for g, m in enumerate(st["trunk"]["members"], 1) if g != group for p in m}
    if other & set(ports):
        raise ValueError(f"port(s) {sorted(other & set(ports))} already belong to another trunk group")
    ps = [v["ds_PortSetting"][i] for i in idx]
    if any(x[1] in (2, 4) for x in ps):
        raise ValueError("a port is set to half duplex; trunk members must be full duplex or Auto")
    if len({x[1] for x in ps}) > 1 or len({x[2] for x in ps}) > 1:
        raise ValueError("trunk members must have the same speed and flow-control setting")
    if len({tuple(v["ds_BWList"][i]) for i in idx}) > 1:
        raise ValueError("trunk members must have the same bandwidth limits")
    if len({v["ds_defPrio"][i] for i in idx}) > 1:
        raise ValueError("trunk members must have the same default 802.1p priority")
    if v.get("ds_QVEnable") and len({v["ds_PVID"][i] for i in idx}) > 1:
        raise ValueError("trunk members must have the same PVID")
    for vl in v.get("ds_QVLANList", []):
        if len({vl[2][i] for i in idx}) > 1:
            raise ValueError(f"trunk members must have the same membership (untagged/tagged/none) in VLAN {vl[0]}")
    if v.get("ds_MirrorEn"):
        if v["ds_MirrTag"] in ports:
            raise ValueError("the mirror's sniffer port cannot be a trunk member")
        src = [v["ds_MirrSrc"][i] for i in idx]
        if "1" in src and "0" in src:
            raise ValueError("either all or none of the trunk members must be mirror source ports")
    if v.get("ds_LBDEn") == 1 and any(v["ds_LBDList"][i][0] == "1" for i in idx):
        raise ValueError("loop detection is on for a trunk member; turn it off for that port first")
    if any(int(m[0]) - 1 in idx for m in v.get("ds_SMacList", [])):
        raise ValueError("a trunk member has a static MAC entry")
    if v.get("ds_TrafficEn") == 1 and any(v["ds_Traffic"][i] == "1" for i in idx):
        raise ValueError("a trunk member is used by traffic segmentation")
    before = {"enabled": st["trunk"]["enabled"], "ports": st["trunk"]["members"][group - 1]}
    params = {"group": group, "ports": ports, "enable_first": not st["trunk"]["enabled"]}
    if before["ports"] == ports and before["enabled"]:
        raise ValueError("nothing to change")
    guard_protected(switch, set(ports) | set(before["ports"]), allow_protected)
    risks = []
    up = [p for p in ports if st["ports"][p - 1]["link"] != "Down"]
    if up:
        risks.append(f"port(s) {up} are up; a trunk needs the same trunk (or none) configured on the device at the other end, or traffic may loop or drop.")
    if params["enable_first"]:
        risks.append("link aggregation is switched on for the whole switch first.")
    return {"op": "dl_trunk", "params": params, "before": before,
            "summary": [f"trunk group {group}: ports {before['ports']} -> {ports}" + ("; trunking switched ON" if params["enable_first"] else "")], "risks": risks}


def plan_trunk_delete(switch: str, group: int, allow_protected: bool = False) -> dict:
    st = _state(switch)
    if not 1 <= group <= dlinkweb.TRUNK_GROUPS:
        raise ValueError(f"trunk group must be 1-{dlinkweb.TRUNK_GROUPS}")
    members = st["trunk"]["members"][group - 1]
    if not members:
        raise ValueError(f"trunk group {group} has no members")
    guard_protected(switch, set(members), allow_protected)
    params = {"group": group, "disable_after": False, "enabled_after": st["trunk"]["enabled"]}
    return {"op": "dl_trunk_delete", "params": params, "before": {"enabled": st["trunk"]["enabled"], "ports": members},
            "summary": [f"delete trunk group {group} (ports {members})"], "risks": ["the ports become independent links again."]}


# ---- apply helpers used by changes.apply_plan -------------------------------------------------------------------------
def current(op: str, st: dict, params: dict):
    if op == "dl_system":
        c = st["system"]
        return {"name": c["name"], "location": c["location"], "timeout": c["login_timeout_minutes"]}
    if op == "dl_save":
        return {"saved": "unknown"}
    if op in ("dl_trunk", "dl_trunk_delete"):
        return {"enabled": st["trunk"]["enabled"], "ports": st["trunk"]["members"][params["group"] - 1]}
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
    if op == "dl_trunk":
        return {"enabled": True, "ports": params["ports"]}
    if op == "dl_trunk_delete":
        return {"enabled": params["enabled_after"], "ports": []}
    if op == "dl_save":
        return {"saved": "unknown"}
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
    elif op == "dl_system":
        s.write_system(p["name"], p["location"], p["timeout"])
    elif op == "dl_save":
        s.save_config()
    elif op == "dl_trunk":
        s.write_trunk(p["group"], p["ports"], p["enable_first"])
    elif op == "dl_trunk_delete":
        s.delete_trunk(p["group"], p["disable_after"])
    else:
        raise ValueError(op)


def inverse(op: str, params: dict, before):
    """(op, params) that undoes a plan, given the state it replaced."""
    if op == "dl_vlan":
        return ("dl_vlan_delete", {"vid": params["vid"]}) if before is None else ("dl_vlan", before)
    if op == "dl_vlan_delete":
        return "dl_vlan", before
    if op == "dl_trunk":
        if not before["ports"]:
            return "dl_trunk_delete", {"group": params["group"], "disable_after": params["enable_first"], "enabled_after": not params["enable_first"]}
        return "dl_trunk", {"group": params["group"], "ports": before["ports"], "enable_first": False}
    if op == "dl_trunk_delete":
        return "dl_trunk", {"group": params["group"], "ports": before["ports"], "enable_first": False}
    return op, before
