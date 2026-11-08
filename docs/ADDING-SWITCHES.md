# Adding or expanding supported switches

Three kinds of work, from least to most:

1. **Another switch of a family that is already supported** (another USRobotics 7724 or D-Link DGS-1100-08): a config entry, nothing else.
2. **Another model or firmware of a supported family** (DGS-1100-16/24, a newer D-Link firmware, another Cameo-built switch): a few constants, some detection, new fixtures.
3. **A new vendor** (TP-Link Easy Smart, Netgear, ...): a new session class, parser, planner and fake, following the same safety rails.

Rule that applies to all three: **read first, write last, and never trust a fake switch alone**. Both vendors' write paths were found by reading the switch's own pages,
and the fake switches (tests/fakeswitch.py, tests/fakedlink.py) only know what we already learned; on 2026-11-08 real hardware showed two behaviours the fakes could not
have predicted (a mirror disable that hangs without the full form, a trunk write that takes 11 s). A write is "proven" only after a round trip on an unused port.

## 1. Another switch of a supported family
1. Make sure the host can reach it (the management VLAN or a routed path) and note its address. `discover(interface)` or `probe(host)` confirms the Cameo discovery reply.
2. Add it to `~/.config/switch-mcp/switches.json`:
   ```json
   "lab-switch": {"host": "192.0.2.10", "vendor": "dlink", "protected_ports": []}
   ```
   `vendor` is `usrobotics` or `dlink`. Add every uplink and every port that carries your management path to `protected_ports` before any write.
3. Give it a password: `SWITCH_MCP_PASSWORD_LAB_SWITCH` (name upper-cased, `-` becomes `_`) or an entry in `~/.config/switch-mcp/passwords.json` (mode 0600). Passwords are never tool arguments.
4. Run `get_state lab-switch` and `backup_state lab-switch` before anything else. Read the port map (which port goes where) by matching the MAC table or the per-port counters, set `protected_ports`, then plan.

## 2. Another model or firmware of a supported family
What is model-specific today (search the code for these):
- **D-Link**: `ROOT = "/DGS-1100-08_1.10.039/"` in `dlinkweb.py` (the firmware folder), `PORTS = 8`, `TRUNK_GROUPS`/`TRUNK_MAX_PORTS`, `TIMEOUT_MIN/MAX`, the speed and state tables, and the quad rule for trunk groups.
  Find the real values on the switch: fetch `index.htm`; it contains `RT='/<model>_<firmware>/';`. `DS/const.js` holds `g_PortNum`, `g_TrunkNum`, `g_MaxTkPtNum`, `g_MinTmOut`, `g_MaxTmOut`.
  A sensible refactor is to read `RT` from `index.htm` at login and the limits from `DS/const.js`, instead of hard-coding them; do that before adding a second D-Link model.
- **USRobotics**: 24 ports and 15 VLAN groups appear in `webui.py` validation (`1 <= port <= 24`, `group` 1-15) and the trunk option sets are read from the page, not hard-coded.

Steps:
1. **Get the pages.** Log in with the existing session class (it handles the login hash/cookies) and save the pages to a scratch directory, read-only. For a D-Link: `index.htm` -> `Prefix.js` -> `en.js` -> `tree/treeitems.js` ->
   the `iss/H_*.htm` pages and the `DS/*.js` data files. `en.js` maps menu names to page files (`HTML0125=ISD+'H_25_Mirroring.htm'`); each page ends in `submitForm(form, "<name>.cgi")`.
   Do not run any page's buttons.
2. **Diff against the supported model.** Compare data variable names (`ds_*`), form control names and CGI names with `docs/PROTOCOL.md`. Differences become constants or parsers; identical pages need no code.
3. **Fixtures.** Save the data files as `tests/fixtures/dlink_<name>.js` with real MACs, serials and IPs replaced by documentation values (RFC 5737 addresses, `00-00-5E-00-53-xx` MACs). The repository is shared; the user's real switches live only in `~/.config`.
4. **Tests before hardware.** Extend the fake to the new port count/limits and make the existing plan tests pass for the new model; add tests for anything the model does differently.
5. **Hardware.** Read-only first (`get_state`, compare with the web page on screen). Then the proof sequence below on a port with no link and not in `protected_ports`.

## 3. A new vendor
Create one module per concern, mirroring what exists:
| File | Job |
|---|---|
| `<vendor>web.py` | the session class: login (always logged out in `__exit__`, one session at a time through the shared lock), an allow-list of read pages, parsers, and **an allow-list of write endpoints that is the only thing `_post` accepts** |
| `<vendor>changes.py` | `plan_*` functions (return `op`, `params`, `before`, `summary`, `risks`; they write nothing), plus `current(op, state, params)`, `target(op, params)`, `write(session, op, params)` and `inverse(op, params, before)` for apply and rollback |
| `server.py` | `@mcp.tool()` wrappers named `<vendor>_plan_*`; the shared `apply_plan` and `backup_state` need no change |
| `webui.open_session` | return the new session class for the new `vendor` value |
| `tests/fake<vendor>.py` | an in-memory switch that mimics the real pages, including quirks you have measured |
Reuse, do not reimplement: `safety.guard_protected` (protected ports), `safety.audit` (the audit log, no secrets), `changes.apply_plan` (re-read and refuse if the switch changed, snapshot, rollback plan written to disk **before** the write,
read-back verification, lost-contact handling).

How to find the write protocol of a vendor that has no API:
1. Fetch the web UI's pages and read the JavaScript: form control names and the URL each form posts to are plain text. Where the UI is a single-page app, read its scripts and watch one request in the browser's network tab.
2. Check what the vendor's own utility sends (the Cameo SmartConsole's SOAP/UDP channel was decoded this way; see `PROTOCOL.md`).
3. Search for existing open-source drivers or MCP servers for the family (`docs/IDEAS.md` lists the TP-Link, Netgear and CLI-based ones) and read their request code before writing your own.
4. Record the form names, the allowed values, and every client-side rule the page enforces in JavaScript before posting (the switch may not re-check them): repeat those rules in the `plan_*` function so a bad request fails at plan time.

## The proof sequence for any new write (on an unused, unprotected port)
1. `backup_state`, and write down the exact state you expect to return to.
2. `plan_*` and read the plan; `apply_plan` with `confirm=true` only after reading it.
3. Check the **read-back** (`verified: true`) and that nothing else changed (compare the full state against the backup: ports, VLANs, mirror, system).
4. Apply the **rollback plan** and compare again. The switch must match the backup (ignoring uptime).
5. If an apply returns "lost contact" or a timeout, do not repeat it blindly: read the state first, then probe the request by hand with a long timeout to see whether the switch is slow (trunks) or never answers (a field it expects is missing).
6. Only then change the README status table from "fake switch only" to "proven on hardware", with the date.

## Things that bit us
- These switches evict a second web session: close any browser tab on the switch first, and never run two tools at once.
- A form field that the page disables in JavaScript is not sent by a browser, but some CGIs still need it (D-Link mirror disable).
- Slow writes are normal on old firmware (USRobotics trunk, ~11 s). Give such writes a long timeout, or a success will be reported as lost contact.
- An empty value may be ignored rather than cleared (USRobotics VLAN description: a single space clears it).
- Changes are live but may not survive a power cut until saved (D-Link: `dlink_plan_save`; USRobotics: from its own page).
- Anything that can lock you out or drop the network (password, IP, reboot, reset, restore) belongs behind `webui.require_maintenance(...)`, with a plan that says what breaks, protected-port refusal, a saved backup first and a verification that does not depend on the session being changed. Firmware upgrade is still not implemented: capture the upload protocol from the vendor tool first.

## Beyond switches
`docs/IDEAS.md` sketches a multi-vendor umbrella (one server, one plan/apply core, vendor drivers behind it). The pieces above are exactly the seam it would use: a driver is a session class plus planners; the core owns safety, audit and rollback.
