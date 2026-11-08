# switch-mcp

An MCP server for two old "smart" switch families that no other MCP covers: the **USRobotics 7724** (24-port gigabit, port-based VLANs) and the
**D-Link DGS-1100** EasySmart series (802.1Q). Both are Cameo-built and share a UDP discovery protocol (see `docs/PROTOCOL.md`).

It reads state and makes changes the careful way: **plan, then apply**. A plan writes nothing; it shows the exact differences and the risks (for VLANs:
which connected ports would stop reaching each other, which ports leave the management VLAN). Applying needs the plan id and `confirm=true`, re-reads the switch
and refuses if it changed, saves a snapshot, writes, reads back to verify, and returns a rollback plan.

## Tools
| Tool | What it does |
|---|---|
| `list_switches`, `list_interfaces` | configured switches; IPv4 interfaces on this host |
| `discover(interface)`, `probe(host)` | UDP discovery (broadcast+multicast), or ask one switch by IP |
| `get_state(switch)` | parsed state: VLANs, ports, mirror (and D-Link system/trunk) |
| `read_page(switch, page)`, `overview(switch)` | raw page data |
| `plan_vlan_group`, `plan_port`, `plan_mirror` | USRobotics changes (plan only) |
| `dlink_plan_vlan`, `dlink_plan_vlan_delete`, `dlink_plan_pvid`, `dlink_plan_port`, `dlink_plan_mirror` | D-Link changes (plan only) |
| `apply_plan(plan_id, confirm=true)` | apply a plan from this server session |

## Setup
```bash
uv sync --extra dev && uv run pytest
mkdir -p ~/.config/switch-mcp && cp switches.example.json ~/.config/switch-mcp/switches.json   # then edit the addresses
```
Passwords are never tool arguments and are never returned. Provide each as `SWITCH_MCP_PASSWORD_<NAME>` (name upper-cased, `-` becomes `_`) or in
`~/.config/switch-mcp/passwords.json` (`{"usr-switch": "..."}`, mode 0600; a looser mode is refused).

**Factory-default passwords** (so a reset switch is usable): both vendors document `admin` as the default (USRobotics quick-install guide; D-Link
EasySmart manuals, which also give 10.90.90.90 as the default D-Link address and 192.168.0.1 for the USRobotics). Change it after a reset.
This repository contains no other credentials; tests use a throwaway `pw`.

Register with Claude Code only for switch work (every MCP server adds tool schemas to every request):
```bash
claude mcp add switch -- uv run --project /path/to/switch-mcp switch-mcp
```

## Safety
- Only five D-Link CGIs and three USRobotics forms can ever be posted. Password, reset, firmware, backup, IP/system, trunk and 802.1Q-mode changes cannot.
- Plans live in server memory for 10 minutes; snapshots go to `~/.local/state/switch-mcp/`.
- One web session at a time (these switches evict a second login), always logged out. If a human has the web page open, close it first.
- The switches apply changes live; they may not keep them across a power loss unless saved from their own web page.

## Status
| Feature | State |
|---|---|
| Discovery (both vendors) | proven on hardware |
| Reads (both) | proven on hardware |
| USRobotics writes: VLAN group, port, mirror | proven on hardware (round trips, switch left as found) |
| D-Link writes: VLAN add/delete | proven on hardware |
| D-Link writes: PVID, port, mirror | tested against a fake switch only |
| Passwords, firmware (TFTP), trunks, system/IP over either channel | not implemented |

## Related projects (other gear with no vendor API)
- [tplink-easysmart-mcp](https://glama.ai/mcp/servers/ebenezer-isaac/tplink-easysmart-mcp): TP-Link Easy Smart switches over their web CGI, with dry-run, protected ports and lockout-aware failures.
- A second TP-Link Easy Smart server (`tplink-easy-smart-switch-mcp`, by qwe7002-ai) and a Netgear XS512EM server (`netgear-xs512em`, by supere989); find them by name on GitHub.
- [NetContext-MCP](https://github.com/hgursel/NetContext-MCP): SSH to older CLI switches (Cisco, Aruba) with legacy algorithms.

The gated-write pattern here (dry run, risk checks, session-eviction caveat) follows the same approach; ideas flow both ways.

See `docs/PROTOCOL.md` for everything learned about the wire formats.
