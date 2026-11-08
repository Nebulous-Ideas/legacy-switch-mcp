import json

import httpx
import pytest
from fakedlink import FakeDLink
from fakeswitch import FakeSwitch

from switch_mcp import changes, dlinkchanges, dlinkweb, reports, safety, webui


@pytest.fixture
def env(monkeypatch, tmp_path):
    usr, dl = FakeSwitch(up_ports=(1, 2, 3, 4)), FakeDLink()
    routes = {"192.0.2.3": usr.handle, "198.51.100.3": dl.handle}
    monkeypatch.setattr(webui, "TRANSPORT", httpx.MockTransport(lambda r: routes[r.url.host](r)))
    monkeypatch.setattr(safety, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_USR_TEST", "pw")
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_DLINK_TEST", "pw")
    # protect the uplinks: port 1 on both switches
    cfg = webui.CONFIG_DIR / "switches.json"
    cfg.write_text(json.dumps({"usr-test": {"host": "192.0.2.3", "vendor": "usrobotics", "protected_ports": [1]},
                               "dlink-test": {"host": "198.51.100.3", "vendor": "dlink", "protected_ports": [1]}}))
    changes.PLANS.clear()
    return usr, dl


def audit_lines():
    f = safety.STATE_DIR / "audit.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


# ---- protected ports
def test_protected_port_refused_on_every_usr_plan(env):
    with pytest.raises(ValueError, match="protected"):
        changes.plan_vlan_group("usr-test", 11, [2, 3], None)           # removes port 1 from the group
    with pytest.raises(ValueError, match="protected"):
        changes.plan_port("usr-test", 1, "100M Full", False, "Normal")
    with pytest.raises(ValueError, match="protected"):
        changes.plan_mirror("usr-test", "Both", 1, 2)
    assert any(e["event"] == "refused" for e in audit_lines())


def test_protected_port_refused_on_every_dlink_plan(env):
    with pytest.raises(ValueError, match="protected"):
        dlinkchanges.plan_vlan("dlink-test", 1, None, [2, 3, 4, 5, 6, 7, 8], [])      # port 1 leaves VLAN 1
    with pytest.raises(ValueError, match="protected"):
        dlinkchanges.plan_port("dlink-test", 1, "100M Full", False)
    with pytest.raises(ValueError, match="protected"):
        dlinkchanges.plan_mirror("dlink-test", "Rx", 1, [2])


def test_unprotected_ports_and_explicit_override_work(env):
    assert changes.plan_vlan_group("usr-test", 11, list(range(1, 24)), None)["status"] == "planned"   # only port 24 touched
    assert changes.plan_port("usr-test", 1, "100M Full", False, "Normal", allow_protected=True)["status"] == "planned"
    assert webui.protected_ports("usr-test") == {1}


# ---- audit log and persistence
def test_audit_log_records_plan_and_apply_without_secrets(env):
    usr, _ = env
    p = changes.plan_port("usr-test", 5, "100M Full", False, "Normal")
    changes.apply_plan(p["id"], True)
    events = [e["event"] for e in audit_lines()]
    assert events[:1] == ["plan"] and "apply-written" in events and "apply" in events
    assert "pw" not in (safety.STATE_DIR / "audit.jsonl").read_text()


def test_rollback_plan_survives_a_restart(env):
    usr, _ = env
    p = changes.plan_port("usr-test", 5, "100M Full", False, "Normal")
    r = changes.apply_plan(p["id"], True)
    assert (safety.STATE_DIR / "plans" / f"{r['rollback_plan_id']}.json").exists()
    changes.PLANS.clear()                                        # a new server process knows nothing
    back = changes.apply_plan(r["rollback_plan_id"], True)       # ...but the rollback plan is on disk
    assert back["verified"] and usr.ports[5]["speed"] == "Auto"


def test_applied_status_persists_and_blocks_a_replay(env):
    p = changes.plan_port("usr-test", 5, "100M Full", False, "Normal")
    changes.apply_plan(p["id"], True)
    changes.PLANS.clear()
    with pytest.raises(ValueError, match="applied"):
        changes.apply_plan(p["id"], True)


def test_plan_ids_cannot_escape_the_plans_folder(env):
    assert changes.get_plan("../../etc/passwd") is None
    assert changes.get_plan("nope") is None


def test_lost_contact_after_writing_is_reported_with_a_rollback(env, monkeypatch):
    usr, _ = env
    p = changes.plan_port("usr-test", 5, "100M Full", False, "Normal")
    real = webui.Session.state
    calls = {"n": 0}

    def flaky(self):
        calls["n"] += 1
        if calls["n"] == 2:          # the read-back after the write
            raise httpx.ConnectError("switch went away")
        return real(self)
    monkeypatch.setattr(webui.Session, "state", flaky)
    r = changes.apply_plan(p["id"], True)
    assert r["applied"] is None and r["verified"] is None and "lost contact" in r["warning"]
    assert changes.get_plan(r["rollback_plan_id"]) is not None
    assert any(e["event"] == "apply-lost-contact" for e in audit_lines())


# ---- reports
def test_backup_and_snapshot_listing(env):
    out = reports.backup_state("usr-test")
    assert (safety.STATE_DIR / "backups").exists() and out["vendor"] == "usrobotics"
    reports.backup_state("dlink-test")
    names = [s["name"] for s in reports.list_snapshots()]
    assert len(names) == 2 and all(n.endswith(".json") for n in names)
    assert [s["name"] for s in reports.list_snapshots("dlink-test")] == [n for n in names if "dlink-test" in n]


def test_describe_both_vendors(env):
    usr_md = reports.describe("usr-test")
    assert "port-based VLANs" in usr_md and "Every group contains every port" in usr_md and "| 01 | 1G Full |" in usr_md
    dl_md = reports.describe("dlink-test")
    assert "802.1Q VLANs" in dl_md and "| 1 | - | 01, 02, 03, 04, 05, 06, 07, 08 | none |" in dl_md
    assert "198.51.100.3" in dl_md
