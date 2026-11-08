"""Configuration/firmware downloads and the gated maintenance operations (password, IP, reboot, factory reset, restore), against the fake switches."""
import json
import os
import stat

import httpx
import pytest
from fakedlink import FakeDLink
from fakeswitch import FakeSwitch

from switch_mcp import changes, dlinkweb, maintenance, safety, webui


@pytest.fixture
def mt(monkeypatch, tmp_path):
    usr, dl = FakeSwitch(up_ports=(1, 2, 3, 4)), FakeDLink()
    routes = {"192.0.2.3": usr.handle, "198.51.100.3": dl.handle, "192.0.2.50": usr.handle, "198.51.100.50": dl.handle}
    monkeypatch.setattr(webui, "TRANSPORT", httpx.MockTransport(lambda r: routes[r.url.host](r)))
    monkeypatch.setattr(safety, "STATE_DIR", tmp_path / "state")
    for n in ("USR_TEST", "DLINK_TEST"):
        monkeypatch.delenv(f"SWITCH_MCP_PASSWORD_{n}", raising=False)
    pw = webui.CONFIG_DIR / "passwords.json"
    pw.write_text(json.dumps({"usr-test": "pw", "dlink-test": "pw"}))
    os.chmod(pw, 0o600)
    monkeypatch.setattr(maintenance, "POLL", 0.0)
    monkeypatch.setattr(maintenance, "DOWN_WAIT", 0.05)
    monkeypatch.setattr(maintenance, "UP_WAIT", 1.0)
    monkeypatch.setattr(maintenance, "_in_use", lambda ip: False)
    changes.PLANS.clear()
    return usr, dl


def enable(monkeypatch):
    monkeypatch.setenv(webui.MAINTENANCE_ENV, "1")


def audit_text():
    f = safety.STATE_DIR / "audit.jsonl"
    return f.read_text() if f.exists() else ""


# ---- downloads ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("switch,blob", [("usr-test", b"USR-CONFIG-DEMO"), ("dlink-test", b"DLINK-CONFIG-DEMO")])
def test_backup_config_is_stored_privately_and_never_returned(mt, switch, blob):
    meta = maintenance.backup_config(switch)
    assert blob.decode() not in json.dumps(meta)
    f = meta["path"]
    assert open(f, "rb").read() == blob and stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert maintenance.list_files(switch)[0]["id"] == meta["id"] and "sha256" not in maintenance.list_files(switch)[0]


def test_firmware_backup_is_dlink_only(mt):
    meta = maintenance.backup_firmware("dlink-test")
    assert meta["kind"] == "firmware" and meta["bytes"] > 1000
    with pytest.raises(ValueError, match="only the D-Link"):
        maintenance.backup_firmware("usr-test")


# ---- the gate ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("planner", [lambda: maintenance.plan_password("dlink-test"), lambda: maintenance.plan_reboot("dlink-test", True)])
def test_apply_is_refused_unless_maintenance_is_enabled(mt, planner):
    usr, dl = mt
    p = planner()
    with pytest.raises(PermissionError, match=webui.MAINTENANCE_ENV):
        changes.apply_plan(p["id"], True)
    assert changes.get_plan(p["id"]).status == "planned" and dl.reboots == 0 and dl.posts == []


def test_the_low_level_writers_refuse_without_the_gate(mt):
    with webui.open_session("dlink-test") as s:
        for call in (lambda: s.change_password("pw", "x"), lambda: s.write_ip("198.51.100.9", "255.255.255.0", "198.51.100.1"), s.reboot, s.factory_reset,
                     lambda: s.upload_config(b"x")):
            with pytest.raises(PermissionError):
                call()
    with webui.open_session("usr-test") as s:
        for call in (lambda: s.change_password("pw", "x"), lambda: s.write_ip("192.0.2.9", "255.255.255.0", "192.0.2.1"), s.factory_reset, lambda: s.upload_config(b"x")):
            with pytest.raises(PermissionError):
                call()


# ---- password ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("switch", ["usr-test", "dlink-test"])
def test_password_change_verifies_stores_and_never_shows_it(mt, monkeypatch, switch):
    enable(monkeypatch)
    p = maintenance.plan_password(switch)
    r = changes.apply_plan(p["id"], True)
    stored = json.loads((webui.CONFIG_DIR / "passwords.json").read_text())
    new = stored[switch]
    assert r["applied"] and r["verified"] and new != "pw" and len(new) == 16 and new.isalnum()
    assert new not in json.dumps(r) and new not in audit_text() and "pw" not in audit_text().replace("password", "")
    assert json.loads((webui.CONFIG_DIR / "passwords.prev.json").read_text()) == {switch: "pw"}
    assert not (webui.CONFIG_DIR / "passwords.pending.json").exists()
    assert stat.S_IMODE(os.stat(webui.CONFIG_DIR / "passwords.json").st_mode) == 0o600
    with webui.open_session(switch):   # the stored password really is the switch's password now
        pass


def test_password_not_changed_by_the_switch_is_reported_and_cleaned_up(mt, monkeypatch):
    usr, dl = mt
    enable(monkeypatch)
    p = maintenance.plan_password("dlink-test")
    monkeypatch.setattr(dlinkweb.DLinkSession, "change_password", lambda self, o, n: None)     # the switch silently ignores the request
    r = changes.apply_plan(p["id"], True)
    assert r["applied"] is False and "old password" in r["warning"]
    assert json.loads((webui.CONFIG_DIR / "passwords.json").read_text())["dlink-test"] == "pw"
    assert not (webui.CONFIG_DIR / "passwords.pending.json").exists()


def test_password_unknown_outcome_keeps_the_pending_file(mt, monkeypatch):
    enable(monkeypatch)
    p = maintenance.plan_password("usr-test")
    monkeypatch.setattr(maintenance, "_login_ok", lambda *a, **k: False)
    r = changes.apply_plan(p["id"], True)
    assert r["applied"] is None and "accepts neither" in r["warning"]
    pending = json.loads((webui.CONFIG_DIR / "passwords.pending.json").read_text())["usr-test"]
    assert pending["old"] == "pw" and len(pending["new"]) == 16 and pending["new"] not in json.dumps(r)


def test_password_plan_refuses_an_environment_password(mt, monkeypatch):
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_DLINK_TEST", "pw")
    with pytest.raises(ValueError, match="environment variable"):
        maintenance.plan_password("dlink-test")


# ---- IP address --------------------------------------------------------------------------------------------------------------
def test_ip_change_needs_the_explicit_flag_and_sane_values(mt, monkeypatch):
    with pytest.raises(ValueError, match="allow_ip_change"):
        maintenance.plan_ip("dlink-test", "198.51.100.50", "255.255.255.0", "198.51.100.1")
    for ip, mask, gw, match in (("198.51.100.50", "255.255.255.0", "203.0.113.1", "same subnet"), ("198.51.100.255", "255.255.255.0", "198.51.100.1", "same subnet"),
                                ("198.51.100.1", "255.255.255.0", "198.51.100.1", "same subnet"), ("198.51.100.3", "255.255.255.0", "198.51.100.1", "already"),
                                ("nonsense", "255.255.255.0", "198.51.100.1", "not a valid")):
        with pytest.raises(ValueError, match=match):
            maintenance.plan_ip("dlink-test", ip, mask, gw, allow_ip_change=True)
    monkeypatch.setattr(maintenance, "_in_use", lambda ip: True)
    with pytest.raises(ValueError, match="already uses it"):
        maintenance.plan_ip("dlink-test", "198.51.100.50", "255.255.255.0", "198.51.100.1", allow_ip_change=True)


@pytest.mark.parametrize("switch,new,gw,attr", [("dlink-test", "198.51.100.50", "198.51.100.1", "dl"), ("usr-test", "192.0.2.50", "192.0.2.1", "usr")])
def test_ip_change_verifies_at_the_new_address_and_updates_switches_json(mt, monkeypatch, switch, new, gw, attr):
    usr, dl = mt
    enable(monkeypatch)
    p = maintenance.plan_ip(switch, new, "255.255.255.0", gw, allow_ip_change=True)
    assert any("NEW address" in r for r in p["risks"])
    r = changes.apply_plan(p["id"], True)
    assert r["applied"] and r["verified"] and r["new_address"] == new
    cfg = webui.CONFIG_DIR
    assert json.loads((cfg / "switches.json").read_text())[switch]["host"] == new and list(cfg.glob("switches.json.bak-*"))
    if attr == "dl":
        assert dl.ipinfo[1] == new
    else:
        assert ".".join(usr.sys["ip"]) == new and usr.sys["sys"] == "demo-usr"     # name and location were sent back unchanged


def test_ip_change_that_cannot_be_verified_does_not_touch_switches_json(mt, monkeypatch):
    enable(monkeypatch)
    p = maintenance.plan_ip("dlink-test", "198.51.100.50", "255.255.255.0", "198.51.100.1", allow_ip_change=True)
    monkeypatch.setattr(maintenance, "_login_ok", lambda *a, **k: False)
    monkeypatch.setattr(maintenance, "UP_WAIT", 0.05)
    r = changes.apply_plan(p["id"], True)
    assert r["applied"] is None and "switches.json was NOT updated" in r["warning"]
    assert json.loads((webui.CONFIG_DIR / "switches.json").read_text())["dlink-test"]["host"] == "198.51.100.3"


# ---- reboot, reset, restore --------------------------------------------------------------------------------------------------
def test_reboot_is_dlink_only_and_respects_protected_ports(mt, monkeypatch):
    usr, dl = mt
    with pytest.raises(ValueError, match="no reboot page"):
        maintenance.plan_reboot("usr-test")
    (webui.CONFIG_DIR / "switches.json").write_text(json.dumps({"dlink-test": {"host": "198.51.100.3", "vendor": "dlink", "protected_ports": [1]}}))
    with pytest.raises(ValueError, match="protected"):
        maintenance.plan_reboot("dlink-test")
    enable(monkeypatch)
    r = changes.apply_plan(maintenance.plan_reboot("dlink-test", allow_protected=True)["id"], True)
    assert dl.reboots == 1 and r["applied"] and r["outage"]["back"] and r["verified"]


def test_factory_reset_saves_state_and_config_first(mt, monkeypatch):
    usr, dl = mt
    enable(monkeypatch)
    p = maintenance.plan_factory_reset("dlink-test")
    assert p["before"]["config_backup"] and maintenance.list_files("dlink-test") and dl.resets == 0
    r = changes.apply_plan(p["id"], True)
    assert dl.resets == 1 and r["applied"] and "10.90.90.90" in r["note"]
    assert maintenance._login_ok("dlink-test") is False       # the stored password no longer fits: the switch is back to "admin"
    ur = changes.apply_plan(maintenance.plan_factory_reset("usr-test")["id"], True)
    assert usr.resets == 1 and "192.168.0.1" in ur["note"]


def test_restore_uses_only_a_stored_file_for_the_same_switch(mt, monkeypatch):
    usr, dl = mt
    meta = maintenance.backup_config("dlink-test")
    with pytest.raises(ValueError, match="no stored config file"):
        maintenance.plan_restore_config("usr-test", meta["id"])            # another switch's file
    with pytest.raises(ValueError, match="no stored config file"):
        maintenance.plan_restore_config("dlink-test", "deadbeef0000")
    p = maintenance.plan_restore_config("dlink-test", meta["id"])
    assert any("EVERYTHING" in r for r in p["risks"])
    enable(monkeypatch)
    r = changes.apply_plan(p["id"], True)
    assert r["applied"] and dl.restored and b"DLINK-CONFIG-DEMO" in dl.restored
    um = maintenance.backup_config("usr-test")
    changes.apply_plan(maintenance.plan_restore_config("usr-test", um["id"])["id"], True)
    assert usr.restored and b"USR-CONFIG-DEMO" in usr.restored


def test_a_tampered_stored_file_is_refused(mt):
    meta = maintenance.backup_config("usr-test")
    open(meta["path"], "ab").write(b"x")
    with pytest.raises(ValueError, match="checksum"):
        maintenance.plan_restore_config("usr-test", meta["id"])


def test_usr_login_with_a_wrong_password_is_refused_even_though_the_reply_has_no_form(mt):
    with pytest.raises(PermissionError):
        with webui.open_session("usr-test", password="wrong"):
            pass
    assert maintenance._login_ok("usr-test") and not maintenance._login_ok("usr-test", password="nope")


def test_rotating_two_switches_keeps_both_previous_passwords(mt, monkeypatch):
    enable(monkeypatch)
    changes.apply_plan(maintenance.plan_password("dlink-test")["id"], True)
    changes.apply_plan(maintenance.plan_password("usr-test")["id"], True)
    assert json.loads((webui.CONFIG_DIR / "passwords.prev.json").read_text()) == {"dlink-test": "pw", "usr-test": "pw"}
    assert not maintenance._login_ok("usr-test", password="pw")        # the old password is really refused after the change


def test_reapply_usr_state_restores_what_a_reset_wipes(mt, monkeypatch):
    usr, dl = mt
    enable(monkeypatch)
    with webui.open_session("usr-test") as s:
        saved = s.state()
    # simulate the mess a factory reset leaves: other names/members, flow control on, no name/location, another address
    usr.groups[9]["desc"], usr.groups[10]["members"] = "x", [1, 2, 3]
    usr.ports[5]["flow"] = True
    usr.sys.update({"sys": "", "loc": "", "ip": ["192", "168", "0", "1"]})
    with webui.open_session("usr-test") as s:
        done = maintenance.reapply_usr_state(s, saved)
    assert done[-1] == "address" and "system" in done and any(d.startswith("vlan") for d in done) and "port 5" in done
    assert usr.sys["sys"] == saved["system"]["name"] and usr.sys["ip"] == ["192", "0", "2", "3"]
    assert usr.groups[9]["desc"] == saved["vlan"][9]["description"] and len(usr.groups[10]["members"]) == 24 and usr.ports[5]["flow"] is False
