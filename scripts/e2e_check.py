"""Drive the switch-mcp server over real MCP stdio against a D-Link, on idle ports only, and leave the switch as it was.

    uv run python scripts/e2e_check.py <dlink-switch> <trunk-group> <port> <port>      e.g.  ... dlink-lab 2 6 7

Checks: every expected tool is registered; get_state; a system-location change and a trunk on the two (idle, unprotected) ports you name, each applied and rolled
back through the tools; a config download (the file is stored, never returned); and that apply_plan refuses a reboot plan while SWITCH_MCP_ENABLE_MAINTENANCE is off.
Do not run it on ports that carry traffic: enabling trunking is a switch-wide setting.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if len(sys.argv) != 5:
    sys.exit(__doc__)
SW, GROUP, P1, P2 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED = {"get_state", "apply_plan", "backup_config", "backup_firmware", "list_files", "dlink_plan_system", "dlink_plan_save", "dlink_plan_trunk",
            "dlink_plan_trunk_delete", "plan_password", "plan_ip", "plan_reboot", "plan_factory_reset", "plan_restore_config", "plan_system", "plan_trunk"}


async def main() -> int:
    env = {k: v for k, v in os.environ.items() if k != "SWITCH_MCP_ENABLE_MAINTENANCE"}   # the gate must be closed for this check
    params = StdioServerParameters(command="uv", args=["run", "--project", ROOT, "switch-mcp"], env=env)
    failures = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, **args):
                res = await s.call_tool(name, args)
                text = res.content[0].text if res.content else ""
                return res.isError, (json.loads(text) if text.startswith(("{", "[")) else text)

            def check(label, ok, detail=""):
                print(("ok   " if ok else "FAIL ") + label + (f"  {detail}" if detail and not ok else ""))
                if not ok:
                    failures.append(label)

            tools = {t.name for t in (await s.list_tools()).tools}
            check("all expected tools registered", EXPECTED <= tools, str(sorted(EXPECTED - tools)))
            err, st0 = await call("get_state", switch=SW)
            check("get_state", not err and st0["trunk"]["members"] is not None, str(st0)[:120])
            if err:
                return 1
            for p in (P1, P2):
                check(f"port {p} is idle", st0["ports"][p - 1]["link"] == "Down", f"link {st0['ports'][p - 1]['link']}")
            if failures:
                print("refusing to continue: the ports are not idle")
                return 1
            for label, tool, args in (("system location", "dlink_plan_system", {"location": "e2e check"}), ("trunk", "dlink_plan_trunk", {"group": GROUP, "ports": [P1, P2]})):
                err, plan = await call(tool, switch=SW, **args)
                check(f"{label}: plan", not err, str(plan)[:150])
                if err:
                    continue
                err, a = await call("apply_plan", plan_id=plan["id"], confirm=True)
                check(f"{label}: apply verified", not err and a.get("verified") is True, str(a)[:150])
                err, u = await call("apply_plan", plan_id=a["rollback_plan_id"], confirm=True)
                check(f"{label}: rollback verified", not err and u.get("verified") is True, str(u)[:150])
            err, c = await call("backup_config", switch=SW)
            stored_ok = (not err) and os.path.exists(c["path"]) and (os.stat(c["path"]).st_mode & 0o777) == 0o600 and os.path.getsize(c["path"]) == c["bytes"] and len(json.dumps(c)) < 1500
            check("backup_config stores a private file and returns only its id, size and checksum", stored_ok, str(c)[:150])
            err, p = await call("plan_reboot", switch=SW, allow_protected=True)
            err2, a = await call("apply_plan", plan_id=p["id"], confirm=True)
            check("reboot apply is refused while maintenance is off", err2 and "SWITCH_MCP_ENABLE_MAINTENANCE" in str(a))
            err, st1 = await call("get_state", switch=SW)
            same = (st1["system"]["location"], st1["trunk"]["enabled"], st1["trunk"]["members"]) == (st0["system"]["location"], st0["trunk"]["enabled"], st0["trunk"]["members"])
            check("switch left as found", same)
    print("RESULT:", "all checks passed" if not failures else f"{len(failures)} failed: {failures}")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
