"""Make some ports of the USRobotics 7724 a private patch: they can only reach each other.

The 7724 has port-based VLANs only, and every group starts with all 24 ports. To isolate ports P, one group keeps only P and every other
group loses P. Dry run by default (prints the edits and checks that no connected port loses a path); --apply performs them one by one
through the same plan/verify/snapshot code as the MCP tools and stops at the first problem.

  uv run python scripts/isolate_patch_ports.py --switch NAME [--ports 23 24] [--group 15] [--name DVR] [--apply] [--force]
"""
import argparse
import sys

from legacy_switch_mcp import changes, webui


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", required=True, help="name from switches.json")
    ap.add_argument("--ports", type=int, nargs="+", default=[23, 24])
    ap.add_argument("--group", type=int, default=15)
    ap.add_argument("--name", default="DVR")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="allow ports that currently have a link")
    a = ap.parse_args()
    ports = sorted(set(a.ports))

    with webui.open_session(a.switch) as s:
        st = s.state()
    up = {p["port"] for p in st["ports"] if p["link"] != "Down"}
    busy = sorted(set(ports) & up)
    if busy and not a.force:
        print(f"refusing: port(s) {busy} have a link up right now (something is plugged in). Pick idle ports or pass --force.")
        return 2

    # the edits, in a safe order: first make the private group, then take the ports out of every other group
    edits = [(a.group, ports, a.name)]
    edits += [(g["group"], [m for m in g["members"] if m not in ports], None) for g in st["vlan"] if g["group"] != a.group and set(ports) & set(g["members"])]

    simulated = [dict(g) for g in st["vlan"]]
    for grp, members, _ in edits:
        next(g for g in simulated if g["group"] == grp)["members"] = members
    lost = sorted(changes._links(st["vlan"], up) - changes._links(simulated, up))
    print(f"{a.switch}: isolating ports {ports} in group {a.group:02d} ({len(edits)} group edits)")
    for grp, members, name in edits:
        print(f"  group {grp:02d}: members -> {' '.join(f'{m:02d}' for m in members) if len(members) < 8 else f'{len(members)} ports'}" + (f"  name {name!r}" if name else ""))
    print(f"connected ports that would lose a path to each other: {lost or 'none'}")
    if lost:
        print("refusing: that would cut a working connection")
        return 3
    if not a.apply:
        print("dry run only; add --apply to do it")
        return 0

    for grp, members, name in edits:
        plan = changes.plan_vlan_group(a.switch, grp, members, name)
        if any("could no longer talk" in r for r in plan["risks"]):
            print(f"stopping before group {grp:02d}: {plan['risks']}")
            return 4
        r = changes.apply_plan(plan["id"], True)
        print(f"  group {grp:02d}: applied={r['applied']} verified={r['verified']}  snapshot {r['snapshot'].rsplit('/', 1)[-1]}")
        if not r["verified"]:
            print("stopping: the switch did not read back as requested")
            return 5
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
