"""Run one maintenance operation from the command line (the same plan -> confirm -> apply code the MCP tools use, with SWITCH_MCP_ENABLE_MAINTENANCE turned on
for this process only). Shows the plan and its risks, then asks you to type the switch name unless --yes is given.

    uv run python scripts/maint.py password <switch>
    uv run python scripts/maint.py ip <switch> <ip> <netmask> <gateway>
    uv run python scripts/maint.py reboot <switch> [--allow-protected]          (D-Link only)
    uv run python scripts/maint.py reset <switch> [--allow-protected]           (factory reset; saves state + config first)
    uv run python scripts/maint.py restore <switch> <file-id> [--allow-protected]   (ids: uv run python scripts/maint.py files [switch])
Close any browser tab on the switch first. Passwords are never printed.
"""
import os
import sys

os.environ["SWITCH_MCP_ENABLE_MAINTENANCE"] = "1"
from legacy_switch_mcp import changes, maintenance  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = {a for a in sys.argv[1:] if a.startswith("--")}
if not args:
    sys.exit(__doc__)
action, rest = args[0], args[1:]
if action == "files":
    for f in maintenance.list_files(*rest[:1]):
        print(f["switch"], f["kind"], f["id"], f["bytes"], "bytes", f["saved_at"])
    sys.exit()
allow = "--allow-protected" in flags
planners = {
    "password": lambda sw: maintenance.plan_password(sw),
    "ip": lambda sw, ip, mask, gw: maintenance.plan_ip(sw, ip, mask, gw, allow_ip_change=True),
    "reboot": lambda sw: maintenance.plan_reboot(sw, allow),
    "reset": lambda sw: maintenance.plan_factory_reset(sw, allow),
    "restore": lambda sw, fid: maintenance.plan_restore_config(sw, fid, allow),
}
if action not in planners:
    sys.exit(__doc__)
sw = rest[0]
plan = planners[action](*rest)
print(f"{sw}: " + " | ".join(plan["summary"]))
for r in plan["risks"]:
    print(" - risk:", r)
if "--yes" not in flags and input(f"type the switch name ({sw}) to continue: ").strip() != sw:
    sys.exit("not confirmed; nothing changed")
result = changes.apply_plan(plan["id"], True)
print("result:", {k: v for k, v in result.items() if k not in ("snapshot",)})
