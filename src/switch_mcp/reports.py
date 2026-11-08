"""Read-only extras: backups of the parsed state, a list of saved snapshots, and a markdown description of a switch."""
from __future__ import annotations

import json
import time

from . import safety, webui


def backup_state(switch: str) -> dict:
    """Save the parsed state of a switch to a JSON file (not the switch's own config file: these switches have no export we can use)."""
    with webui.open_session(switch) as s:
        st = s.state()
    vendor = webui.load_switches()[switch]["vendor"]
    d = safety.STATE_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{switch}.json"
    path.write_text(json.dumps({"switch": switch, "vendor": vendor, "taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "state": st}, indent=1))
    safety.audit("backup", switch=switch, path=str(path))
    return {"path": str(path), "vendor": vendor}


def list_snapshots(switch: str | None = None) -> list[dict]:
    """Backups and the snapshots saved before each apply, newest first."""
    out = []
    for folder, kind in ((safety.STATE_DIR, "snapshot"), (safety.STATE_DIR / "backups", "backup")):
        if folder.is_dir():
            for f in folder.glob("*.json"):
                if switch is None or f"-{switch}" in f.name:
                    out.append({"kind": kind, "name": f.name, "path": str(f), "bytes": f.stat().st_size, "modified": f.stat().st_mtime})
    return sorted(out, key=lambda x: -x["modified"])


def _ports(ns: list[int]) -> str:
    return ", ".join(f"{p:02d}" for p in ns) if ns else "none"


def markdown(switch: str, vendor: str, st: dict) -> str:
    """A readable description of a switch's configuration, suitable for a wiki page."""
    lines = [f"# {switch}", ""]
    if vendor == "dlink":
        sysd = st["system"]
        lines += [f"D-Link EasySmart, address {sysd['ip']}/{sysd['netmask']}, gateway {sysd['gateway']}, system name `{sysd['name']}`.", "",
                  "## Ports", "", "| Port | Link | Speed setting | Flow control | PVID |", "|---|---|---|---|---|"]
        lines += [f"| {p['port']:02d} | {p['link']} | {p['speed_set']} | {'on' if p['flow_control'] else 'off'} | {p['pvid']} |" for p in st["ports"]]
        v = st["vlan"]
        lines += ["", "## 802.1Q VLANs" + ("" if v["enabled_802_1q"] else " (mode is OFF)"), "", "| VID | Name | Untagged | Tagged |", "|---|---|---|---|"]
        lines += [f"| {x['vid']} | {x['name'] or '-'} | {_ports(x['untagged'])} | {_ports(x['tagged'])} |" for x in v["vlans"]]
        m = st["mirror"]
        lines += ["", "## Mirror", "", "Off." if not m["enabled"] else f"{m['mode']}: sniffer port {m['sniffer_port']:02d} copies {_ports(m['source_ports'])}.",
                  "", f"Trunks: {'on' if st['trunk']['enabled'] else 'off'}. Loop detection: {'on' if st['loop_detection']['enabled'] else 'off'}."]
    else:
        groups, all_ports = st["vlan"], {p["port"] for p in st["ports"]}
        lines += ["USRobotics smart switch with port-based VLANs (it cannot tag or untag 802.1Q).", "",
                  "## Ports", "", "| Port | Link | Speed setting | Flow control | QoS |", "|---|---|---|---|---|"]
        lines += [f"| {p['port']:02d} | {p['link']} | {p['speed_set']} | {p['flow_set']} | {p['qos']} |" for p in st["ports"]]
        full = [g for g in groups if set(g["members"]) == all_ports]
        lines += ["", "## VLAN groups", ""]
        if len(full) == len(groups):
            lines.append("Every group contains every port, so no ports are isolated from each other.")
        else:
            lines += ["| Group | Name | Members |", "|---|---|---|"]
            lines += [f"| {g['group']:02d} | {g['description'] or '-'} | {'all ports' if set(g['members']) == all_ports else _ports(g['members'])} |" for g in groups]
        m = st["mirror"]
        lines += ["", "## Mirror", "", "Off." if m["mode"] == "Disable" else f"{m['mode']}: sniffer port {m['sniffer_port']:02d} copies port {m['source_port']:02d}."]
    return "\n".join(lines) + "\n"


def describe(switch: str) -> str:
    with webui.open_session(switch) as s:
        st = s.state()
    return markdown(switch, webui.load_switches()[switch]["vendor"], st)
