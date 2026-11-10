"""Rotate a switch's web password from the command line (same code path as the plan_password tool, without needing the MCP server restarted).

    uv run python scripts/rotate_password.py dlink-pve
    uv run python scripts/rotate_password.py usr-switch

Asks you to type the switch name first. The new password is generated here, verified by logging in with it, then stored in passwords.json (the old one goes to
passwords.prev.json). It is never printed. If verification fails, the new password stays in passwords.pending.json in the config directory.
Close any browser tab on the switch first: these switches allow one web session at a time.
"""
import json
import os
import stat
import sys

os.environ["SWITCH_MCP_ENABLE_MAINTENANCE"] = "1"
from legacy_switch_mcp import changes, maintenance, webui  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)
sw = sys.argv[1]
plan = maintenance.plan_password(sw)
print(f"{sw}: {plan['summary'][0]}")
for r in plan["risks"]:
    print(" - risk:", r)
if input(f"type the switch name ({sw}) to continue: ").strip() != sw:
    sys.exit("not confirmed; nothing changed")
result = changes.apply_plan(plan["id"], True)
print("result:", {k: v for k, v in result.items() if k != "snapshot"})
cfg = webui.CONFIG_DIR
print("passwords.json mode:", oct(stat.S_IMODE(os.stat(cfg / "passwords.json").st_mode)), "| previous kept:", (cfg / "passwords.prev.json").exists(),
      "| pending file left:", (cfg / "passwords.pending.json").exists())
print("stored password is still the factory default:", json.loads((cfg / "passwords.json").read_text())[sw] == "admin")
