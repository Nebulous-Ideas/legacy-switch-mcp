"""Backups of the switches' configuration and firmware files, and the gated maintenance operations: password, IP address, reboot, factory reset
and configuration restore.

Downloads only read from the switch. Everything else needs the server to be started with SWITCH_MCP_ENABLE_MAINTENANCE=1, goes through the same plan ->
confirm -> apply path as the other changes, saves a snapshot first and is written to the audit log (never with a password). None of these can be proven by a
fake switch alone: the README says which have been run on hardware.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import string
import subprocess
import time

import httpx

from . import dlinkweb, safety, webui
from .safety import audit, guard_protected

POLL = 3.0          # seconds between reachability checks after a reboot, reset, restore or IP change
DOWN_WAIT = 25.0    # how long to watch for the switch going away
UP_WAIT = 240.0     # how long to wait for it to come back
DEFAULTS = {"dlink": "10.90.90.90", "usrobotics": "192.168.0.1"}   # documented factory addresses; the factory password is "admin" for both
ALNUM = string.ascii_letters + string.digits


# ---- stored files (configuration and firmware downloads) ---------------------------------------------------------------------
def _files_dir(switch: str):
    d = safety.STATE_DIR / "files" / switch
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _store(switch: str, kind: str, data: bytes) -> dict:
    ts = time.strftime("%Y%m%d-%H%M%S")
    sha = hashlib.sha256(data).hexdigest()
    path = _files_dir(switch) / f"{ts}-{kind}-{sha[:8]}.bin"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    meta = {"id": sha[:12], "switch": switch, "kind": kind, "bytes": len(data), "sha256": sha, "saved_at": ts, "path": str(path)}
    path.with_suffix(".json").write_text(json.dumps(meta))
    audit("file-saved", switch=switch, kind=kind, id=meta["id"], bytes=len(data))
    return meta


def list_files(switch: str | None = None) -> list[dict]:
    out = []
    for f in sorted((safety.STATE_DIR / "files").glob("*/*.json")):
        m = json.loads(f.read_text())
        if switch is None or m["switch"] == switch:
            out.append({k: m[k] for k in ("id", "switch", "kind", "bytes", "saved_at", "path")})
    return sorted(out, key=lambda m: m["saved_at"], reverse=True)


def _load_file(switch: str, file_id: str, kind: str) -> tuple[dict, bytes]:
    for f in (safety.STATE_DIR / "files" / switch).glob("*.json") if (safety.STATE_DIR / "files" / switch).exists() else []:
        m = json.loads(f.read_text())
        if m["id"] == file_id and m["kind"] == kind:
            data = open(m["path"], "rb").read()
            if hashlib.sha256(data).hexdigest() != m["sha256"]:
                raise ValueError(f"stored file {file_id} no longer matches its checksum; refusing to use it")
            return m, data
    raise ValueError(f"no stored {kind} file {file_id!r} for {switch!r}; make one with backup_config first (list_files shows them)")


def backup_config(switch: str) -> dict:
    """Download the switch's configuration file into the state directory (mode 0600). The content is never returned: the D-Link file holds the admin password."""
    with webui.open_session(switch) as s:
        meta = _store(switch, "config", s.download_config())
    return {**meta, "note": "stored privately (mode 0600); treat it as a secret. It can be restored with plan_restore_config."}


def backup_firmware(switch: str) -> dict:
    """Download the D-Link's running firmware image (read-only) into the state directory. The USRobotics web interface offers no firmware download."""
    with webui.open_session(switch) as s:
        if not isinstance(s, dlinkweb.DLinkSession):
            raise ValueError("only the D-Link offers a firmware download; the USRobotics has none in its web interface")
        meta = _store(switch, "firmware", s.download_firmware())
    return {**meta, "note": "a copy of the image now running. Keep it before any firmware change; this server cannot upgrade firmware."}


# ---- plans -------------------------------------------------------------------------------------------------------------------
def _new_plan(switch: str, op: str, params: dict, before, summary: list[str], risks: list[str]) -> dict:
    from . import changes
    return changes._register(changes._new(switch, op, params, before, summary, risks))


def _vendor(switch: str) -> str:
    sw = webui.load_switches()
    if switch not in sw:
        raise KeyError(f"unknown switch {switch!r}; known: {sorted(sw)}")
    return sw[switch]["vendor"]


def _all_ports_guard(switch: str, allow_protected: bool, what: str) -> list[str]:
    prot = webui.protected_ports(switch)
    if prot and not allow_protected:
        audit("refused", switch=switch, reason=f"{what} touches every port", ports=sorted(prot))
        raise ValueError(f"{what} drops every port, including the protected ones {sorted(prot)}. If this is deliberate, plan again with allow_protected=true.")
    return [f"every port drops for the duration, including the protected ports {sorted(prot)}."] if prot else ["every port drops for the duration."]


def plan_password(switch: str) -> dict:
    vendor = _vendor(switch)
    env = "SWITCH_MCP_PASSWORD_" + switch.upper().replace("-", "_")
    if os.environ.get(env):
        raise ValueError(f"this switch's password comes from the environment variable {env}, which this server cannot update; move it into passwords.json first")
    path = webui.CONFIG_DIR / "passwords.json"
    if not path.exists() or switch not in json.loads(path.read_text()):
        raise ValueError(f"{path} has no entry for {switch!r}; the new password has nowhere to be stored")
    webui.load_password(switch)   # also checks the file mode
    risks = ["the web interface sends passwords in clear text over HTTP; anyone who can sniff the management network sees the change." if vendor == "dlink"
             else "the web interface sends passwords in clear text over HTTP (it has no HTTPS).",
             "if verification fails, the new password stays in passwords.pending.json in the config directory; the switch may then have it.",
             f"a factory reset restores the default password (admin) and the default address {DEFAULTS[vendor]}."]
    return _new_plan(switch, "maint_password", {"length": 16}, None,
                     ["generate a random 16-character password, change it on the switch, verify by logging in with it, then store it in passwords.json "
                      "(the old one is kept in passwords.prev.json). The password is never shown."], risks)


def _in_use(ip: str) -> bool:
    return subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True).returncode == 0


def plan_ip(switch: str, ip: str, netmask: str, gateway: str, allow_ip_change: bool = False) -> dict:
    if not allow_ip_change:
        raise ValueError("changing the management address cuts the session and can strand the switch; plan again with allow_ip_change=true if you mean it")
    vendor = _vendor(switch)
    try:
        net = ipaddress.ip_network(f"{ip}/{netmask}", strict=False)
        addr, gw = ipaddress.ip_address(ip), ipaddress.ip_address(gateway)
    except ValueError as e:
        raise ValueError(f"not a valid address/mask/gateway: {e}")
    if addr not in net or gw not in net or addr == gw or addr in (net.network_address, net.broadcast_address):
        raise ValueError("the address and gateway must be different usable hosts in the same subnet")
    host = webui.load_switches()[switch]["host"]
    if ip == host:
        raise ValueError("that is already the switch's address")
    if _in_use(ip):
        raise ValueError(f"{ip} answers a ping: another device already uses it")
    with webui.open_session(switch) as s:
        cur = s.state()["system"]
    cur_ip = cur["ip"] if vendor == "usrobotics" else cur["ip"]
    return _new_plan(switch, "maint_ip", {"ip": ip, "netmask": netmask, "gateway": gateway}, {"ip": cur_ip, "netmask": cur["netmask"], "gateway": cur["gateway"]},
                     [f"management address {cur_ip}/{cur['netmask']} gw {cur['gateway']} -> {ip}/{netmask} gw {gateway}",
                      "afterwards the new address is checked by logging in, and switches.json is updated (the old file is kept as switches.json.bak-<time>)"],
                     ["the current session ends as soon as the switch changes address; the rollback plan has to be applied at the NEW address.",
                      "if the new address is not reachable from this host (wrong VLAN or routing), you will have to reach the switch some other way or reset it.",
                      "update anything else that uses the old address (monitoring, Proxmox network config, the firewall, DNS)."])


def plan_reboot(switch: str, allow_protected: bool = False) -> dict:
    if _vendor(switch) != "dlink":
        raise ValueError("the USRobotics web interface has no reboot page")
    risks = _all_ports_guard(switch, allow_protected, "a reboot") + ["the switch is unreachable for about a minute; unsaved configuration is lost (use dlink_plan_save first)."]
    return _new_plan(switch, "maint_reboot", {}, None, ["reboot the switch"], risks)


def plan_factory_reset(switch: str, allow_protected: bool = False) -> dict:
    vendor = _vendor(switch)
    risks = _all_ports_guard(switch, allow_protected, "a factory reset")
    state = __import__("switch_mcp.reports", fromlist=["backup_state"]).backup_state(switch)
    cfg = backup_config(switch)
    return _new_plan(switch, "maint_reset", {}, {"state_backup": state["path"], "config_backup": cfg["id"]},
                     [f"factory reset: every setting, the address and the password return to defaults (address {DEFAULTS[vendor]}, password admin)",
                      f"saved first: parsed state {state['path']}, configuration file id {cfg['id']}"],
                     risks + [f"after the reset the switch is reachable only at {DEFAULTS[vendor]} (from a host on that subnet) and its web login is admin; this server's stored address and password stop working.",
                              "restoring the saved configuration file brings the old settings back."])


def plan_restore_config(switch: str, file_id: str, allow_protected: bool = False) -> dict:
    vendor = _vendor(switch)
    meta, _ = _load_file(switch, file_id, "config")
    risks = _all_ports_guard(switch, allow_protected, "a configuration restore")
    risks += ["the switch reboots and takes on EVERYTHING in the file: VLANs, ports, address and password; check that the file is the one you mean.",
              "if the file holds a different address, the switch will come back there, not at the current one."]
    return _new_plan(switch, "maint_restore", {"file_id": file_id}, {"file": meta["saved_at"], "bytes": meta["bytes"]},
                     [f"restore configuration file {file_id} (saved {meta['saved_at']}, {meta['bytes']} bytes) onto {switch}"], risks)


# ---- apply -------------------------------------------------------------------------------------------------------------------
def _snapshot(plan, st) -> str:
    safety.STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap = safety.STATE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{plan.switch}-{plan.op}-{plan.id}.json"
    snap.write_text(json.dumps({"plan": plan.public(), "state": st}, indent=1))
    return str(snap)


def _login_ok(switch: str, password: str | None = None, switches: dict | None = None) -> bool:
    try:
        with webui.open_session(switch, password=password, switches=switches):
            return True
    except (PermissionError, httpx.HTTPError, OSError):
        return False


def _wait_down_then_up(switch: str, switches: dict | None = None) -> dict:
    t0, went_down = time.time(), False
    while time.time() - t0 < DOWN_WAIT:
        if not _login_ok(switch, switches=switches):
            went_down = True
            break
        time.sleep(POLL)
    t1 = time.time()
    while time.time() - t0 < DOWN_WAIT + UP_WAIT:
        if _login_ok(switch, switches=switches):
            return {"went_down": went_down, "back": True, "seconds_until_back": round(time.time() - t0)}
        time.sleep(POLL)
    return {"went_down": went_down, "back": False, "waited_seconds": round(time.time() - t0)}


def _write_secret_json(path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _apply_password(plan) -> dict:
    sw = plan.switch
    old = webui.load_password(sw)
    path = webui.CONFIG_DIR / "passwords.json"
    data = json.loads(path.read_text())
    new = "".join(secrets.choice(ALNUM) for _ in range(plan.params["length"]))
    pending = webui.CONFIG_DIR / "passwords.pending.json"
    _write_secret_json(pending, {sw: {"old": old, "new": new, "started": time.strftime("%Y%m%dT%H%M%S")}})   # the way back exists before anything is written
    with webui.open_session(sw) as s:
        snap = _snapshot(plan, s.state())
        try:
            s.change_password(old, new)
        except (httpx.HTTPError, OSError) as e:
            audit("password-lost-contact", switch=sw, error=type(e).__name__)
    if _login_ok(sw, password=new):
        prev_path = webui.CONFIG_DIR / "passwords.prev.json"
        prev = json.loads(prev_path.read_text()) if prev_path.exists() else {}
        prev[sw] = old                       # keep other switches' previous passwords (found when rotating a second switch overwrote the first)
        _write_secret_json(prev_path, prev)
        data[sw] = new
        _write_secret_json(path, data)
        pending.unlink(missing_ok=True)
        audit("password-changed", switch=sw, plan=plan.id)
        return {"applied": True, "verified": True, "snapshot": snap,
                "note": f"the new password is stored in {path}; the previous one is kept in passwords.prev.json. Neither is shown here."}
    if _login_ok(sw, password=old):
        pending.unlink(missing_ok=True)
        audit("password-not-changed", switch=sw, plan=plan.id)
        return {"applied": False, "verified": True, "snapshot": snap, "warning": "the switch still accepts the old password: nothing changed."}
    audit("password-unknown", switch=sw, plan=plan.id)
    return {"applied": None, "verified": False, "snapshot": snap,
            "warning": f"the switch accepts neither password. The new one is in {pending}; try it from the switch's web page. If that fails, reset the switch "
                       f"(default {DEFAULTS[_vendor(sw)]} / admin)."}


def _apply_ip(plan) -> dict:
    sw, p = plan.switch, plan.params
    switches = webui.load_switches()
    with webui.open_session(sw) as s:
        snap = _snapshot(plan, s.state())
        try:
            s.write_ip(p["ip"], p["netmask"], p["gateway"])
        except (httpx.HTTPError, OSError) as e:
            audit("ip-lost-contact", switch=sw, error=type(e).__name__)
    new_switches = {**switches, sw: {**switches[sw], "host": p["ip"]}}
    deadline = time.time() + UP_WAIT
    while time.time() < deadline:
        if _login_ok(sw, switches=new_switches):
            cfg = webui.CONFIG_DIR / "switches.json"
            if cfg.exists():
                bak = cfg.with_name(f"switches.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
                bak.write_text(cfg.read_text())
                cur = json.loads(cfg.read_text())
                if sw in cur:
                    cur[sw]["host"] = p["ip"]
                    cfg.write_text(json.dumps(cur, indent=2))
            audit("ip-changed", switch=sw, plan=plan.id, new=p["ip"])
            return {"applied": True, "verified": True, "snapshot": snap, "new_address": p["ip"],
                    "note": f"logged in at {p['ip']}; switches.json updated (old file kept as switches.json.bak-*). Update monitoring, Proxmox/network config, firewall and DNS that used the old address."}
        time.sleep(POLL)
    audit("ip-unverified", switch=sw, plan=plan.id)
    return {"applied": None, "verified": False, "snapshot": snap,
            "warning": f"could not log in at {p['ip']} within {int(UP_WAIT)} s. The switch may have taken the address but be unreachable from this host (VLAN/routing), "
                       f"or not have changed. switches.json was NOT updated. Old address {plan.before['ip']}."}


def _apply_simple(plan, action: str) -> dict:
    sw = plan.switch
    with webui.open_session(sw) as s:
        snap = _snapshot(plan, s.state())
        if action == "reboot":
            s.reboot()
        elif action == "reset":
            s.factory_reset()
        else:
            _, data = _load_file(sw, plan.params["file_id"], "config")
            try:
                s.upload_config(data)
            except (httpx.HTTPError, OSError) as e:
                audit("restore-lost-contact", switch=sw, error=type(e).__name__)
    if action == "reset":
        audit("factory-reset", switch=sw, plan=plan.id)
        return {"applied": True, "verified": None, "snapshot": snap,
                "note": f"reset sent. The switch is now at {DEFAULTS[_vendor(sw)]} with password admin (from a host on that subnet); this server's stored address and password no longer apply."}
    out = _wait_down_then_up(sw)
    audit(action, switch=sw, plan=plan.id, **out)
    note = ("the switch is reachable again." if out["back"] else "the switch did not answer again in time; check it at its address (a restored file may have changed the address).")
    return {"applied": True, "verified": out["back"], "snapshot": snap, "outage": out, "note": note}


def apply(plan) -> dict:
    """Called by changes.apply_plan for maint_* plans, after the confirm/status/expiry checks."""
    webui.require_maintenance(plan.op.removeprefix("maint_"))
    audit("maint-apply-start", id=plan.id, switch=plan.switch, op=plan.op)
    if plan.op == "maint_password":
        return _apply_password(plan)
    if plan.op == "maint_ip":
        return _apply_ip(plan)
    return _apply_simple(plan, {"maint_reboot": "reboot", "maint_reset": "reset", "maint_restore": "restore"}[plan.op])
