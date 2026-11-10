# legacy-switch-mcp

[![CI](https://github.com/Nebulous-Ideas/legacy-switch-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Nebulous-Ideas/legacy-switch-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)
![MCP server](https://img.shields.io/badge/MCP-server-8A2BE2.svg)

<!-- mcp-name: io.github.Nebulous-Ideas/legacy-switch-mcp -->

> **Use at your own risk.** This tool can change VLANs, reboot switches and factory-reset them, and a bad change can cut you off from the switch.
> It has been proven on hardware on exactly two models (USRobotics 7724, D-Link DGS-1100-08) and is fake-switch tested only elsewhere. Read the plan before applying it,
> list your uplinks in `protected_ports`, and keep the maintenance switch off unless you need it. Provided as is, without warranty (see `LICENSE`).

An MCP server for two old "smart" switch families that no other MCP covers: the **USRobotics 7724** (24-port gigabit, port-based VLANs) and the
**D-Link DGS-1100** EasySmart series (802.1Q). Both are Cameo-built and share a UDP discovery protocol (see `docs/PROTOCOL.md`).

It reads state and makes changes the careful way: **plan, then apply**. A plan writes nothing; it shows the exact differences and the risks (for VLANs:
which connected ports would stop reaching each other, which ports leave the management VLAN). Applying needs the plan id and `confirm=true`, re-reads the switch
and refuses if it changed, saves a snapshot, writes, reads back to verify, and returns a rollback plan.

## Install
No clone needed (needs [uv](https://docs.astral.sh/uv/)). First create `~/.config/switch-mcp/switches.json` and a password for each switch (see Setup below), then add the server to your MCP client:
```bash
claude mcp add switch -- uvx legacy-switch-mcp                                    # Claude Code
claude mcp add switch --env SWITCH_MCP_TOOLSETS=read -- uvx legacy-switch-mcp     # look-only (fewest tokens)
```
Claude Desktop, Cursor and other clients take the same command in their JSON config:
```json
{"mcpServers": {"switch": {"command": "uvx", "args": ["legacy-switch-mcp"], "env": {"SWITCH_MCP_TOOLSETS": "read"}}}}
```
Until the package is on PyPI, use `uvx --from git+https://github.com/Nebulous-Ideas/legacy-switch-mcp legacy-switch-mcp`.

## Tools
| Tool | What it does |
|---|---|
| `list_switches`, `list_interfaces` | configured switches; IPv4 interfaces on this host |
| `discover(interface)`, `probe(host)` | UDP discovery (broadcast+multicast), or ask one switch by IP |
| `get_state(switch)` | parsed state: VLANs, ports, mirror (and D-Link system/trunk) |
| `read_page(switch, page)`, `overview(switch)` | raw page data |
| `backup_state`, `list_snapshots`, `describe_switch` | save the parsed state; list saved states; markdown description for a wiki |
| `backup_config`, `backup_firmware`, `list_files` | download the configuration (both) / firmware (D-Link) file into the state directory, mode 0600; the content is never returned |
| `plan_password`, `plan_ip`, `plan_reboot`, `plan_factory_reset`, `plan_restore_config` | maintenance plans (both vendors; reboot D-Link only). **Applying needs the server started with `SWITCH_MCP_ENABLE_MAINTENANCE=1`** |
| `plan_vlan_group`, `plan_port`, `plan_mirror`, `plan_trunk`, `plan_system` | USRobotics changes (plan only) |
| `dlink_plan_vlan`, `dlink_plan_vlan_delete`, `dlink_plan_pvid`, `dlink_plan_port`, `dlink_plan_mirror`, `dlink_plan_trunk`, `dlink_plan_trunk_delete`, `dlink_plan_system`, `dlink_plan_save` | D-Link changes (plan only) |
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

Register with Claude Code only for switch work (every MCP server adds its tool descriptions to every request of the session):
```bash
claude mcp add switch -- uv run --project /path/to/legacy-switch-mcp legacy-switch-mcp
```

### Keeping the session cost small
Only the tools that can be used are registered. Measured with all 33 tools registered the schema text was about 4,350 tokens; now:
| Setup | Tools | Tokens per request (approx.) |
|---|---|---|
| default: read + files + a set for each vendor in `switches.json`, maintenance off | 28 (both vendors) | 2,450 |
| default + `SWITCH_MCP_ENABLE_MAINTENANCE=1` (adds the 5 maintenance plan tools) | 33 | 2,850 |
| `SWITCH_MCP_TOOLSETS=read,files,dlink` (D-Link work only) | 23 | 1,800 |
| `SWITCH_MCP_TOOLSETS=read` (look only, no plan or apply tools) | 10 | 600 |
`SWITCH_MCP_TOOLSETS` takes a comma list of `read`, `files`, `usr`, `dlink`, `maintenance`, or `all`; `apply_plan` is registered whenever any plan set is. A typo is an error, not a silent fallback.
Maintenance plan tools are not registered at all unless the maintenance switch is on (or `maintenance` is named in `SWITCH_MCP_TOOLSETS`); a registered one still refuses to apply without the switch.
For day-to-day looking: `claude mcp add switch --env SWITCH_MCP_TOOLSETS=read -- uv run ...`; for a change session leave it unset; for maintenance add `--env SWITCH_MCP_ENABLE_MAINTENANCE=1`.

## Testing
```bash
uv sync --extra dev && uv run pytest            # 112 tests against the fake switches; no hardware needed
uv run python scripts/e2e_check.py <dlink-switch> <trunk-group> <port> <port>   # real MCP stdio check against a D-Link, on IDLE ports only
```
`e2e_check.py` starts the server as a client would, round-trips a system-location change and a trunk on the two idle ports you name (each rolled back), downloads a config, and
checks that a reboot plan is refused while maintenance is off. Run it only against a switch where those ports have no link; it leaves the switch as it found it.
Hardware-only checks (outages, factory reset, password) are in `scripts/maint.py`, `scripts/rotate_password.py` and `scripts/recover_after_reset.py`; see `docs/ADDING-SWITCHES.md` for the proof sequence.

## Safety
- **Protected ports**: list uplinks in `switches.json` (`"protected_ports": [24]`); plans that touch them are refused unless `allow_protected=true`.
- **Rollback survives failure**: the rollback plan is written to disk before any write, so if the switch stops answering mid-change it can still be applied from a new session.
- **Audit log**: every plan, refusal and apply is appended to `~/.local/state/switch-mcp/audit.jsonl` (no credentials).
- **Maintenance is off by default.** Password, IP address, reboot, factory reset and config restore plan normally but refuse to apply unless the server was started with
  `SWITCH_MCP_ENABLE_MAINTENANCE=1` (`claude mcp add switch --env SWITCH_MCP_ENABLE_MAINTENANCE=1 -- uv run ...`). The low-level writers refuse too. Passwords are generated by the server,
  verified by logging in, stored in `passwords.json` (old one kept in `passwords.prev.json`; a `passwords.pending.json` exists while a change is in flight) and never shown or logged. An IP change refuses
  unless `allow_ip_change=true`, if the address answers a ping, or if it is outside the gateway's subnet, and updates `switches.json` only after logging in at the new address. Reboot, reset and restore drop every port,
  so they are refused while `protected_ports` exist unless `allow_protected=true`; a factory reset first saves the parsed state and the configuration file; a restore only accepts a file this server saved for the same switch (checksum checked).
- Only ten D-Link CGIs (VLAN add/delete, PVID, port, mirror, system name/location/timeout, save, three trunk CGIs) and five USRobotics forms (VLAN group, port, mirror, trunk, system name/location/timeout)
  can ever be posted in normal operation. Behind the maintenance switch: five more D-Link CGIs (password, IP, reboot, reset, restore) and four USRobotics forms (password, system with IP, reset, restore);
  two D-Link CGIs only download a file. Firmware upgrade, 802.1Q-mode changes and anything else cannot be posted.
- Plans live in server memory for 10 minutes; snapshots go to `~/.local/state/switch-mcp/`.
- One web session at a time (these switches evict a second login), always logged out. If a human has the web page open, close it first.
- The switches apply changes live; they may not keep them across a power loss unless saved from their own web page.

## Status
| Feature | State |
|---|---|
| Discovery (both vendors) | proven on hardware |
| Reads (both) | proven on hardware |
| USRobotics writes: VLAN group, port, mirror | proven on hardware (round trips, switch left as found) |
| USRobotics write: trunk | proven on hardware 2026-11-08 (ports 17+18, both idle; answers in ~11 s, so the write has a 45 s timeout) |
| USRobotics write: system name/location/timeout | proven on hardware 2026-11-08 (location set and restored; the page also holds the IP settings, which are read back and re-sent unchanged, and did not change) |
| D-Link writes: VLAN add/delete, PVID, port, mirror | proven on hardware 2026-11-08 (unused port 8, VLAN 999; every write verified by read-back and rolled back, switch left identical to its backup) |
| D-Link writes: system name/location/timeout, save configuration | proven on hardware 2026-11-08 (location set and restored; save has no read-back) |
| D-Link writes: trunks | proven on hardware 2026-11-08 (idle ports 6+7: trunking switched on, group created, then removed and trunking switched off; no ping loss on the other ports) |
| The MCP tool layer (33 tools over stdio) | proven against a real D-Link with `scripts/e2e_check.py` (plan -> apply -> rollback through the registered tools, gate refusal, downloads) |

### Found on hardware (the fake switches could not have told us)
- **USRobotics login check was wrong**: the reply to a wrong password has no login form (it only appears on the next page), so every password looked valid. Logins now fetch a real page and require it not to be the login form. This also made "verify the new password by logging in" meaningful for the USRobotics.
- **D-Link mirror disable**: posting only `cEn=0` to `PortMirroring.cgi` gets no answer at all and applies nothing (ReadTimeout, repeatable). The switch answers in 0.1 s when the form also carries the current sniffer port, mode and source ports. Fixed; the fake now hangs the same way so a regression fails a test.
- **USRobotics trunk**: the write applies but takes about 11.4 s to answer, longer than the 10 s default timeout; it was reported as "lost contact" although it had worked. Trunk writes now wait up to 45 s.

### Maintenance operations (behind `SWITCH_MCP_ENABLE_MAINTENANCE=1`)
| Item | State |
|---|---|
| Config download (USRobotics `GET config.bin`, D-Link `backup.cgi` with `pswType`) | proven on hardware 2026-11-08, read-only. The D-Link file contains the admin password in clear text: it is stored 0600 and never returned |
| Firmware download (D-Link `FWBackup.cgi`, ~740 KB) | proven on hardware 2026-11-08, read-only. The USRobotics web interface has no firmware page |
| USRobotics system name/location/timeout | proven on hardware 2026-11-08 (location changed and restored; the IP settings were re-sent unchanged and did not change) |
| Password change (both) | proven on hardware 2026-11-08 (run by the operator with `scripts/rotate_password.py`, verified: the old password is refused, the stored new one works) |
| IP change (both) | proven on hardware 2026-11-08: D-Link 198.51.100.3 -> .4 -> .3 and USRobotics 192.0.2.3 -> .9 -> .3 (shown here as documentation addresses), each verified by logging in at the new address; `switches.json` updated, old address stopped answering |
| Reboot (D-Link; the USRobotics has no reboot page) | proven on hardware: back in about 7 s, ~2 s of ping loss behind it, uptime reset, saved password survived (save the configuration first: `dlink_plan_save`) |
| Factory reset + config restore, **D-Link** | proven on hardware: reset -> `10.90.90.90`/`admin` -> upload the saved config file -> back at its own address with its rotated password and a state identical to the pre-test backup (~4 s of cluster-link ping loss) |
| Factory reset, **USRobotics** | proven on hardware, but the recovery is different: after the reset, uploading the switch's own `config.bin` (it reboots and answers a timeout) restored **none** of the VLAN groups (only group 1 remained), flow control (back to Enable), name/location, address (192.168.0.1) or password (`admin`). Recovery means re-applying a saved state (`backup_state`) over the web pages: `maintenance.reapply_usr_state`, used by `scripts/recover_after_reset.py usr-3com <state.json> --state`. The same steps were done by hand on 2026-11-08 and the switch ended identical to its backup; the packaged function itself is fake-switch tested only |
| Config restore, USRobotics | the upload is accepted and the switch reboots (~30 s), but see above: do not rely on it to restore settings after a reset |

### Operational notes from the outage tests
- In the test setup the D-Link carried only a redundant second link and the USRobotics carried the main LAN plus the first link: a D-Link reboot/restore/reset cost a few seconds of ping loss on that link, a USRobotics reset/restore takes the whole LAN down for about a minute. Do one switch at a time.
- After a USRobotics factory reset its password is `admin` again and the stored entry must say so before `plan_password` can rotate it (the recovery sets it; rotate again afterwards).
- Reaching a reset switch at its factory address needs a temporary host route on the machine running the tools (`ip addr add <x>/32` + `ip route add <default>/32 dev <bridge>`), not a /8 or /24 alias that would capture other traffic.

### Not implemented
| Item | Why |
|---|---|
| Firmware upgrade (D-Link `FWUpgrade.cgi`; the vendor utility uses TFTP) | the upload protocol behind the button is unknown (the page only has buttons), and a bad image or a power cut during the write can brick the switch. `backup_firmware` keeps a copy of the running image. |
| The vendor utility's SOAP/UDP channel for writes (`SetDeviceCfg`, TFTP) | not captured; the web channel covers everything above. |
| DHCP (instead of a static address), 802.1Q mode switch | no need so far; the D-Link `set_ip.cgi` supports DHCP, which would make the switch's address unpredictable. |

See `docs/ADDING-SWITCHES.md` for how to add another switch, another model or firmware, or a new vendor.

## Related projects (other gear with no vendor API)
- [tplink-easysmart-mcp](https://glama.ai/mcp/servers/ebenezer-isaac/tplink-easysmart-mcp): TP-Link Easy Smart switches over their web CGI, with dry-run, protected ports and lockout-aware failures.
- A second TP-Link Easy Smart server (`tplink-easy-smart-switch-mcp`, by qwe7002-ai) and a Netgear XS512EM server (`netgear-xs512em`, by supere989); find them by name on GitHub.
- [NetContext-MCP](https://github.com/hgursel/NetContext-MCP): SSH to older CLI switches (Cisco, Aruba) with legacy algorithms.

The gated-write pattern here (dry run, risk checks, session-eviction caveat) follows the same approach; ideas flow both ways.

See `docs/PROTOCOL.md` for the wire formats and the page/CGI map and `docs/IDEAS.md` for what was borrowed from the projects above and a sketch of a multi-vendor umbrella (an idea only).

---
A Nebulous Ideas project, built by the Nebulous Collective: the people and agents who work on it.
