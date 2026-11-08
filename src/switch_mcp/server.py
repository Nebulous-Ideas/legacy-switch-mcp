"""MCP server for the homelab's two Cameo-built smart switches: reads, plus plan/apply writes on both."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import changes, discovery, dlinkchanges, dlinkweb, webui

mcp = FastMCP("switch-mcp")


@mcp.tool()
def list_switches() -> dict:
    """The switches this server knows about (name, address, vendor). Edit ~/.config/switch-mcp/switches.json to add more."""
    return webui.load_switches()


@mcp.tool()
def list_interfaces() -> list[dict]:
    """IPv4 interfaces on this host that discovery can use."""
    return discovery.list_interfaces()


@mcp.tool()
def discover(interface: str, wait_seconds: float = 3.0) -> list[dict]:
    """Broadcast/multicast discovery on one interface (for example vmbr0 or vmbr1). Returns model, name, location, address, netmask, gateway.
    Sends discovery requests only; changes nothing on any switch."""
    return discovery.discover(interface, wait=min(max(wait_seconds, 1.0), 10.0))


@mcp.tool()
def probe(host: str) -> dict:
    """Ask one switch by IP address, which also works across subnets. Returns its discovery reply or {"error": ...}."""
    result = discovery.probe(host)
    return result or {"error": f"no reply from {host}"}


@mcp.tool()
def read_page(switch: str, page: str) -> dict:
    """Read one page of a switch's web interface. USRobotics pages: status, port, vlan, trunk, mirror, statistics, system, trap (tables and form
    controls). D-Link pages: system, vlan, port, mirror, pvlan, mgmt_vlan, trunk (the raw data variables). Needs the switch password in
    SWITCH_MCP_PASSWORD_<NAME> or ~/.config/switch-mcp/passwords.json. Read-only; always logs out."""
    switches = webui.load_switches()
    if switch in switches and switches[switch]["vendor"] == "dlink":
        if page not in dlinkweb.DATA_FILES:  # refuse before logging in
            raise ValueError(f"page must be one of {sorted(dlinkweb.DATA_FILES)}")
    elif page not in webui.READ_PAGES:
        raise ValueError(f"page must be one of {sorted(webui.READ_PAGES)}")
    with webui.open_session(switch) as s:
        return s.read(page)


@mcp.tool()
def overview(switch: str) -> dict:
    """Everything useful in one login. USRobotics: the status, port, vlan, trunk, mirror and system pages. D-Link: the parsed state."""
    with webui.open_session(switch) as s:
        if isinstance(s, dlinkweb.DLinkSession):
            return s.state()
        return {p: s.read(p) for p in ("status", "port", "vlan", "trunk", "mirror", "system")}


@mcp.tool()
def get_state(switch: str) -> dict:
    """Parsed state of either switch. USRobotics: port-based VLAN groups, port status, mirror. D-Link: system, ports (link, speed, PVID),
    802.1Q VLANs (tagged/untagged ports), mirror, trunk, loop detection. Read-only."""
    with webui.open_session(switch) as s:
        return s.state()


@mcp.tool()
def plan_vlan_group(switch: str, group: int, ports: list[int], description: str | None = None) -> dict:
    """PLAN (does not write) a change of one port-based VLAN group (1-15) on a USRobotics switch: its member ports and optionally its
    8-character description. Returns a plan id, the exact differences, and risks such as ports that would stop reaching each other.
    Show the plan to the user, then call apply_plan."""
    return changes.plan_vlan_group(switch, group, ports, description)


@mcp.tool()
def plan_port(switch: str, port: int, speed: str, flow_control: bool = False, qos: str = "Normal") -> dict:
    """PLAN (does not write) a port setting. speed: Auto, 100M Full, 100M Half, 10M Full, 10M Half or Disable. qos: Normal or High.
    flow_control only with Auto. Returns a plan id, differences and risks."""
    return changes.plan_port(switch, port, speed, flow_control, qos)


@mcp.tool()
def plan_mirror(switch: str, mode: str, sniffer_port: int | None = None, source_port: int | None = None) -> dict:
    """PLAN (does not write) the port mirror: mode Disable, Rx, Tx or Both; sniffer_port receives a copy of source_port's traffic."""
    return changes.plan_mirror(switch, mode, sniffer_port, source_port)


@mcp.tool()
def dlink_plan_vlan(switch: str, vid: int, untagged: list[int], tagged: list[int], name: str | None = None) -> dict:
    """PLAN (does not write) creating or editing an 802.1Q VLAN on a D-Link switch: which ports are untagged members and which tagged.
    Repeats the web page's rules (a port is untagged in only one VLAN) and reports risks such as ports leaving the management VLAN.
    Does not change a port's PVID: use dlink_plan_pvid for that. Show the plan to the user, then call apply_plan."""
    return changes.register_dlink(switch, dlinkchanges.plan_vlan(switch, vid, name, untagged, tagged))


@mcp.tool()
def dlink_plan_vlan_delete(switch: str, vid: int) -> dict:
    """PLAN (does not write) deleting a D-Link 802.1Q VLAN (not VLAN 1, not the management VLAN, not one a port uses as its PVID)."""
    return changes.register_dlink(switch, dlinkchanges.plan_vlan_delete(switch, vid))


@mcp.tool()
def dlink_plan_pvid(switch: str, pvids: dict[int, int]) -> dict:
    """PLAN (does not write) setting the PVID (the VLAN that untagged traffic from a port joins) for some D-Link ports: {port: vid}.
    A port's PVID must be a VLAN it is a member of."""
    return changes.register_dlink(switch, dlinkchanges.plan_pvid(switch, {int(k): v for k, v in pvids.items()}))


@mcp.tool()
def dlink_plan_port(switch: str, port: int, speed: str = "Auto", flow_control: bool = False) -> dict:
    """PLAN (does not write) one D-Link port: speed Auto, 10M Half, 10M Full, 100M Half, 100M Full, 1000M Full or Disable."""
    return changes.register_dlink(switch, dlinkchanges.plan_port(switch, port, speed, flow_control))


@mcp.tool()
def dlink_plan_mirror(switch: str, mode: str, sniffer_port: int | None = None, source_ports: list[int] | None = None) -> dict:
    """PLAN (does not write) the D-Link port mirror: mode Disable, Rx, Tx or Both; sniffer_port gets a copy of the source ports' traffic."""
    return changes.register_dlink(switch, dlinkchanges.plan_mirror(switch, mode, sniffer_port, source_ports or []))


@mcp.tool()
def apply_plan(plan_id: str, confirm: bool = False) -> dict:
    """APPLY a plan made in this server session. Needs confirm=true, which must only be set after the user has seen the plan and agreed.
    Re-reads the switch first and refuses if it changed, saves a snapshot, writes, reads back and verifies, and returns a rollback plan id.
    The switch applies the setting live but may not keep it across a power cut unless saved from its own web page."""
    return changes.apply_plan(plan_id, confirm)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
