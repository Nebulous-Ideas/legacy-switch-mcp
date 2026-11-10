"""MCP server for Cameo-built smart switches (USRobotics 7724, D-Link DGS-1100): reads, plus plan/apply writes on both.

Every registered tool adds its description to every request of the session, so only the tools that can be used are registered (see TOOLSETS):
  read         always: discovery, reads, state backups (10 tools)
  files        configuration/firmware downloads and their listing (3)
  usr          USRobotics plans, when a usrobotics switch is configured (5)
  dlink        D-Link plans, when a dlink switch is configured (9)
  maintenance  password, IP, reboot, factory reset, restore plans, only when SWITCH_MCP_ENABLE_MAINTENANCE=1 (5)
apply_plan is registered whenever any plan tool is. SWITCH_MCP_TOOLSETS (comma list, or "all") overrides the automatic choice, e.g. "read" for look-only sessions.
Descriptions are kept short on purpose; docs/PROTOCOL.md and the README carry the detail.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import changes, discovery, dlinkchanges, dlinkweb, maintenance, reports, webui

mcp = FastMCP("switch-mcp")


# ---- read ---------------------------------------------------------------------------------------------------------------------
def list_switches() -> dict:
    """Configured switches (name, address, vendor); edit ~/.config/switch-mcp/switches.json to add more."""
    return webui.load_switches()


def list_interfaces() -> list[dict]:
    """IPv4 interfaces on this host that discovery can use."""
    return discovery.list_interfaces()


def discover(interface: str, wait_seconds: float = 3.0) -> list[dict]:
    """Broadcast/multicast discovery on one interface (e.g. vmbr0). Sends requests only; changes nothing."""
    return discovery.discover(interface, wait=min(max(wait_seconds, 1.0), 10.0))


def probe(host: str) -> dict:
    """Ask one switch by IP (works across subnets); returns its discovery reply or {"error": ...}."""
    result = discovery.probe(host)
    return result or {"error": f"no reply from {host}"}


def read_page(switch: str, page: str) -> dict:
    """One raw page of a switch's web UI. USRobotics: status, port, vlan, trunk, mirror, statistics, system, trap. D-Link: system, vlan, port, mirror, pvlan, mgmt_vlan, trunk. Read-only."""
    switches = webui.load_switches()
    if switch in switches and switches[switch]["vendor"] == "dlink":
        if page not in dlinkweb.DATA_FILES:  # refuse before logging in
            raise ValueError(f"page must be one of {sorted(dlinkweb.DATA_FILES)}")
    elif page not in webui.READ_PAGES:
        raise ValueError(f"page must be one of {sorted(webui.READ_PAGES)}")
    with webui.open_session(switch) as s:
        return s.read(page)


def overview(switch: str) -> dict:
    """Everything useful in one login (USRobotics: its main pages; D-Link: the parsed state)."""
    with webui.open_session(switch) as s:
        if isinstance(s, dlinkweb.DLinkSession):
            return s.state()
        return {p: s.read(p) for p in ("status", "port", "vlan", "trunk", "mirror", "system")}


def get_state(switch: str) -> dict:
    """Parsed state: VLANs/groups, ports, mirror, trunks, system. Read-only."""
    with webui.open_session(switch) as s:
        return s.state()


def backup_state(switch: str) -> dict:
    """Save the parsed state to a JSON file under ~/.local/state/switch-mcp/backups. Read-only on the switch."""
    return reports.backup_state(switch)


def list_snapshots(switch: str | None = None) -> list[dict]:
    """Saved state files (backups and the snapshot before every apply), newest first."""
    return reports.list_snapshots(switch)


def describe_switch(switch: str) -> str:
    """Markdown description of a switch's configuration for a wiki. Read-only."""
    return reports.describe(switch)


# ---- files --------------------------------------------------------------------------------------------------------------------
def backup_config(switch: str) -> dict:
    """Download the configuration file to the state directory (mode 0600; it is a secret). Returns only id, size, checksum. Read-only."""
    return maintenance.backup_config(switch)


def backup_firmware(switch: str) -> dict:
    """Download the D-Link's running firmware image (~740 KB) to the state directory. Read-only; the USRobotics has none."""
    return maintenance.backup_firmware(switch)


def list_files(switch: str | None = None) -> list[dict]:
    """Saved configuration/firmware files (id, size, time; never content), newest first."""
    return maintenance.list_files(switch)


# ---- usr ----------------------------------------------------------------------------------------------------------------------
def plan_vlan_group(switch: str, group: int, ports: list[int], description: str | None = None, allow_protected: bool = False) -> dict:
    """PLAN (writes nothing) USRobotics port-based VLAN group 1-15: members and optional 8-char description. Shows differences and risks; then apply_plan."""
    return changes.plan_vlan_group(switch, group, ports, description, allow_protected)


def plan_port(switch: str, port: int, speed: str, flow_control: bool = False, qos: str = "Normal", allow_protected: bool = False) -> dict:
    """PLAN a USRobotics port: speed Auto/100M Full/100M Half/10M Full/10M Half/Disable, qos Normal/High, flow_control only with Auto."""
    return changes.plan_port(switch, port, speed, flow_control, qos, allow_protected)


def plan_mirror(switch: str, mode: str, sniffer_port: int | None = None, source_port: int | None = None, allow_protected: bool = False) -> dict:
    """PLAN the USRobotics mirror: mode Disable/Rx/Tx/Both; sniffer_port gets a copy of source_port."""
    return changes.plan_mirror(switch, mode, sniffer_port, source_port, allow_protected)


def plan_system(switch: str, name: str | None = None, location: str | None = None, login_timeout_minutes: int | None = None) -> dict:
    """PLAN the USRobotics name, location (15 chars) and web login timeout (3-30 min). IP settings are re-sent unchanged."""
    return changes.plan_system(switch, name, location, login_timeout_minutes)


def plan_trunk(switch: str, group: int, ports: list[int], allow_protected: bool = False) -> dict:
    """PLAN a USRobotics trunk group 1-3; each offers fixed port sets (get_state trunk[].options); [] disables."""
    return changes.plan_trunk(switch, group, ports, allow_protected)


# ---- dlink --------------------------------------------------------------------------------------------------------------------
def dlink_plan_vlan(switch: str, vid: int, untagged: list[int], tagged: list[int], name: str | None = None, allow_protected: bool = False) -> dict:
    """PLAN creating/editing a D-Link 802.1Q VLAN (untagged and tagged ports). Does not change PVIDs (dlink_plan_pvid)."""
    return changes.register_dlink(switch, dlinkchanges.plan_vlan(switch, vid, name, untagged, tagged, allow_protected))


def dlink_plan_vlan_delete(switch: str, vid: int, allow_protected: bool = False) -> dict:
    """PLAN deleting a D-Link VLAN (not VLAN 1, the management VLAN, or one used as a PVID)."""
    return changes.register_dlink(switch, dlinkchanges.plan_vlan_delete(switch, vid, allow_protected))


def dlink_plan_pvid(switch: str, pvids: dict[int, int], allow_protected: bool = False) -> dict:
    """PLAN D-Link PVIDs as {port: vid}; a port must be a member of its PVID's VLAN."""
    return changes.register_dlink(switch, dlinkchanges.plan_pvid(switch, {int(k): v for k, v in pvids.items()}, allow_protected))


def dlink_plan_port(switch: str, port: int, speed: str = "Auto", flow_control: bool = False, allow_protected: bool = False) -> dict:
    """PLAN a D-Link port: speed Auto/10M Half/10M Full/100M Half/100M Full/1000M Full/Disable."""
    return changes.register_dlink(switch, dlinkchanges.plan_port(switch, port, speed, flow_control, allow_protected))


def dlink_plan_mirror(switch: str, mode: str, sniffer_port: int | None = None, source_ports: list[int] | None = None, allow_protected: bool = False) -> dict:
    """PLAN the D-Link mirror: mode Disable/Rx/Tx/Both; sniffer_port gets a copy of source_ports."""
    return changes.register_dlink(switch, dlinkchanges.plan_mirror(switch, mode, sniffer_port, source_ports or [], allow_protected))


def dlink_plan_system(switch: str, name: str | None = None, location: str | None = None, login_timeout_minutes: int | None = None) -> dict:
    """PLAN the D-Link name, location (20 chars) and web login timeout (3-30 min)."""
    return changes.register_dlink(switch, dlinkchanges.plan_system(switch, name, location, login_timeout_minutes))


def dlink_plan_save(switch: str) -> dict:
    """PLAN saving the D-Link's running configuration to flash (survives a power cut). Saves everything running."""
    return changes.register_dlink(switch, dlinkchanges.plan_save(switch))


def dlink_plan_trunk(switch: str, group: int, ports: list[int], allow_protected: bool = False) -> dict:
    """PLAN a D-Link trunk: group 1 = ports 1-4, group 2 = ports 5-8, 2-4 members with matching settings. Turns trunking on switch-wide if off."""
    return changes.register_dlink(switch, dlinkchanges.plan_trunk(switch, group, ports, allow_protected))


def dlink_plan_trunk_delete(switch: str, group: int, allow_protected: bool = False) -> dict:
    """PLAN removing all members of a D-Link trunk group."""
    return changes.register_dlink(switch, dlinkchanges.plan_trunk_delete(switch, group, allow_protected))


# ---- maintenance (registered only when enabled; applying also needs SWITCH_MCP_ENABLE_MAINTENANCE=1) --------------------------------
def plan_password(switch: str) -> dict:
    """PLAN a new random web password (never shown; verified by login, stored in passwords.json)."""
    return maintenance.plan_password(switch)


def plan_ip(switch: str, ip: str, netmask: str, gateway: str, allow_ip_change: bool = False) -> dict:
    """PLAN a new static management address; needs allow_ip_change=true, refused if in use or outside the gateway's subnet."""
    return maintenance.plan_ip(switch, ip, netmask, gateway, allow_ip_change)


def plan_reboot(switch: str, allow_protected: bool = False) -> dict:
    """PLAN a D-Link reboot (every port drops; USRobotics has no reboot page)."""
    return maintenance.plan_reboot(switch, allow_protected)


def plan_factory_reset(switch: str, allow_protected: bool = False) -> dict:
    """PLAN a factory reset; saves state and config first. Address and password return to defaults."""
    return maintenance.plan_factory_reset(switch, allow_protected)


def plan_restore_config(switch: str, file_id: str, allow_protected: bool = False) -> dict:
    """PLAN restoring a configuration file saved by backup_config (list_files); the switch reboots."""
    return maintenance.plan_restore_config(switch, file_id, allow_protected)


# ---- apply --------------------------------------------------------------------------------------------------------------------
def apply_plan(plan_id: str, confirm: bool = False) -> dict:
    """APPLY a plan. confirm=true only after the user has seen the plan and agreed. Re-reads the switch, snapshots, writes, verifies, returns a rollback plan id."""
    return changes.apply_plan(plan_id, confirm)


TOOLSETS: dict[str, list] = {
    "read": [list_switches, list_interfaces, discover, probe, read_page, overview, get_state, backup_state, list_snapshots, describe_switch],
    "files": [backup_config, backup_firmware, list_files],
    "usr": [plan_vlan_group, plan_port, plan_mirror, plan_system, plan_trunk],
    "dlink": [dlink_plan_vlan, dlink_plan_vlan_delete, dlink_plan_pvid, dlink_plan_port, dlink_plan_mirror, dlink_plan_system, dlink_plan_save,
              dlink_plan_trunk, dlink_plan_trunk_delete],
    "maintenance": [plan_password, plan_ip, plan_reboot, plan_factory_reset, plan_restore_config],
}
PLAN_SETS = {"usr", "dlink", "maintenance"}


def selected_toolsets(environ: dict | None = None, switches: dict | None = None) -> set[str]:
    """SWITCH_MCP_TOOLSETS if set ("all" or a comma list), else read + files + a set per configured vendor + maintenance only when it is enabled."""
    env = os.environ if environ is None else environ
    raw = (env.get("SWITCH_MCP_TOOLSETS") or "").strip()
    if raw:
        names = set(TOOLSETS) if raw == "all" else {n.strip() for n in raw.split(",") if n.strip()}
        unknown = names - set(TOOLSETS)
        if unknown:
            raise ValueError(f"unknown SWITCH_MCP_TOOLSETS {sorted(unknown)}; choose from {sorted(TOOLSETS)} or 'all'")
        return names
    try:
        vendors = {s["vendor"] for s in (webui.load_switches() if switches is None else switches).values()}
    except (OSError, ValueError):
        vendors = set()
    chosen = {"read", "files"}
    chosen |= {"usr"} if "usrobotics" in vendors else set()
    chosen |= {"dlink"} if "dlink" in vendors else set()
    chosen |= {"maintenance"} if env.get(webui.MAINTENANCE_ENV) == "1" else set()
    return chosen


def register_tools(server: FastMCP, toolsets: set[str]) -> list[str]:
    names: list[str] = []
    for ts in TOOLSETS:
        if ts in toolsets:
            for fn in TOOLSETS[ts]:
                server.add_tool(fn)
                names.append(fn.__name__)
    if toolsets & PLAN_SETS:
        server.add_tool(apply_plan)
        names.append("apply_plan")
    return names


def _strip_titles(node) -> None:
    """The generated input schemas carry a cosmetic "title" on every argument ("Switch", "Allow Protected", ...): about 10% of the schema text, no information."""
    if isinstance(node, dict):
        if isinstance(node.get("title"), str):
            node.pop("title")
        for k, v in list(node.items()):
            if k == "properties" and isinstance(v, dict):
                for prop in v.values():
                    _strip_titles(prop)
            else:
                _strip_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_titles(v)


def slim_schemas(server: FastMCP) -> None:
    """Best effort: the arguments are still validated by the generated models; only the description JSON sent to the client gets smaller."""
    try:
        for tool in server._tool_manager.list_tools():
            _strip_titles(tool.parameters)
    except AttributeError:
        pass   # a future SDK without this internal: keep the titles


register_tools(mcp, selected_toolsets())
slim_schemas(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
