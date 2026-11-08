"""After a factory reset (or a restore that changed the address): log in at the switch's DEFAULT address with the default password, upload a saved configuration
file, and wait for the switch to come back at its normal address with its stored password. Your computer needs a route to the default address first, e.g.
    ip addr add 10.90.90.99/32 dev <bridge on the switch's subnet> ; ip route add 10.90.90.90/32 dev <bridge>      (remove both afterwards)

    uv run python scripts/recover_after_reset.py <switch> <file-id> [--host 10.90.90.90] [--password admin]          (D-Link: uploads the saved config file)
    uv run python scripts/recover_after_reset.py usr-3com <state.json> --state [--host 192.168.0.1] [--password admin]   (USRobotics: re-applies a saved state)
Defaults: D-Link 10.90.90.90, USRobotics 192.168.0.1, password admin. `scripts/maint.py files <switch>` lists the saved file ids; saved states are in
~/.local/state/switch-mcp/backups/. Why two ways: on the USRobotics 7724, uploading its own config.bin after a reset restored NONE of the VLAN groups, flow control,
name/location, address or password (tested 2026-11-08), while the D-Link's file restored everything including the password. After either, the switch's password is the one
in the file (D-Link) or `admin` (USRobotics): rotate it again with scripts/rotate_password.py.
"""
import json
import os
import sys

os.environ["SWITCH_MCP_ENABLE_MAINTENANCE"] = "1"
from switch_mcp import maintenance, webui  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
opts = dict(zip(sys.argv[1:], sys.argv[2:]))
if len(args) < 2:
    sys.exit(__doc__)
sw, fid = args[0], args[1]
vendor = webui.load_switches()[sw]["vendor"]
host = opts.get("--host", maintenance.DEFAULTS[vendor])
password = opts.get("--password", "admin")
temp = {**webui.load_switches(), sw: {**webui.load_switches()[sw], "host": host}}
if "--state" in sys.argv:
    state = json.load(open(fid))
    state = state.get("state", state)
    with webui.open_session(sw, password=password, switches=temp) as s:
        print(f"logged in at {host}; re-applying the saved state {fid}")
        print("re-applied:", maintenance.reapply_usr_state(s, state))
else:
    _, data = maintenance._load_file(sw, fid, "config")
    with webui.open_session(sw, password=password, switches=temp) as s:
        print(f"logged in at {host}; uploading configuration file {fid} ({len(data)} bytes)")
        try:
            s.upload_config(data)
        except Exception as e:   # the switch reboots while answering
            print("upload ended with", type(e).__name__, "(expected if the switch rebooted)")
out = maintenance._wait_down_then_up(sw)
print("back at the normal address:", out)
