import pytest

from fakedlink import FakeDLink
from switch_mcp import changes, safety, dlinkchanges, dlinkweb, webui


@pytest.fixture
def dl(monkeypatch, tmp_path):
    fake = FakeDLink()
    monkeypatch.setattr(webui, "TRANSPORT", fake.transport())
    monkeypatch.setattr(safety, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_DLINK_TEST", "pw")
    changes.PLANS.clear()
    return fake


def plan(planned):
    return changes.register_dlink("dlink-test", planned)


def test_state_reads_mirror_mode(dl):
    with webui.open_session("dlink-test") as s:
        st = s.state()
    assert st["mirror"] == {"enabled": False, "mode": "Disable", "sniffer_port": None, "source_ports": []}


def test_untagged_port_cannot_join_a_second_vlan(dl):
    with pytest.raises(ValueError, match="only one VLAN"):
        dlinkchanges.plan_vlan("dlink-test", 11, "IoT", [5], [])
    assert dl.posts == []


def test_plan_writes_nothing_and_flags_management_risk(dl):
    p = plan(dlinkchanges.plan_vlan("dlink-test", 1, None, [1, 2, 3, 4, 6, 7, 8], []))
    assert dl.posts == []
    assert any("port(s) 05" in r and "management VLAN" in r for r in p["risks"])


def test_move_a_port_to_a_new_vlan_end_to_end_and_rollback(dl):
    # 1) take port 5 out of VLAN 1, 2) create VLAN 11 with port 5 untagged and port 1 tagged, 3) point port 5's PVID at it
    a = changes.apply_plan(plan(dlinkchanges.plan_vlan("dlink-test", 1, None, [1, 2, 3, 4, 6, 7, 8], []))["id"], True)
    assert a["verified"] and dl.vlans[0][2] == "UUUU0UUU"
    b = changes.apply_plan(plan(dlinkchanges.plan_vlan("dlink-test", 11, "IoT", [5], [1]))["id"], True)
    assert b["verified"] and [11, "IoT", "T000U000"] in dl.vlans
    c_plan = plan(dlinkchanges.plan_pvid("dlink-test", {5: 11}))
    assert any("port 05 is up" in r and "VLAN 1 to VLAN 11" in r for r in c_plan["risks"])
    c = changes.apply_plan(c_plan["id"], True)
    assert c["verified"] and dl.pvids[4] == 11 and dl.pvids[0] == 1
    # undo everything, newest first
    assert changes.apply_plan(c["rollback_plan_id"], True)["verified"] and dl.pvids == [1] * 8
    assert changes.apply_plan(b["rollback_plan_id"], True)["verified"] and [v[0] for v in dl.vlans] == [1]
    assert changes.apply_plan(a["rollback_plan_id"], True)["verified"] and dl.vlans[0][2] == "UUUUUUUU"
    assert dl.logins == dl.logouts and dl.logins >= 9


def test_pvid_must_be_a_member_vlan(dl):
    with pytest.raises(ValueError, match="not a member of VLAN 11"):
        dlinkchanges.plan_pvid("dlink-test", {5: 11})


def test_delete_refused_while_a_port_uses_the_pvid(dl):
    dl.vlans.append([11, "IoT", "0000U000"])
    dl.vlans[0][2] = "UUUU0UUU"
    dl.pvids[4] = 11
    with pytest.raises(ValueError, match="PVID"):
        dlinkchanges.plan_vlan_delete("dlink-test", 11)
    with pytest.raises(ValueError, match="cannot be deleted"):
        dlinkchanges.plan_vlan_delete("dlink-test", 1)


def test_apply_needs_confirm_and_stale_plans_are_refused(dl):
    p = plan(dlinkchanges.plan_port("dlink-test", 1, "100M Full", False))
    assert any("forced speed" in r for r in p["risks"])
    with pytest.raises(ValueError, match="confirm"):
        changes.apply_plan(p["id"], False)
    dl.ports[0] = [7, 5, 0]  # someone else changed it
    with pytest.raises(RuntimeError, match="changed since"):
        changes.apply_plan(p["id"], True)
    assert dl.posts == []


def test_port_speed_and_flow(dl):
    p = plan(dlinkchanges.plan_port("dlink-test", 6, "Auto", True))
    assert changes.apply_plan(p["id"], True)["verified"]
    assert dl.ports[5] == [0, 1, 1]
    q = plan(dlinkchanges.plan_port("dlink-test", 6, "Disable", False))
    assert changes.apply_plan(q["id"], True)["verified"] and dl.ports[5][1] == 0


def test_mirror_enable_and_disable(dl):
    p = plan(dlinkchanges.plan_mirror("dlink-test", "Both", 2, [3, 4]))
    assert any("sniffer port 02 is up" in r for r in p["risks"])
    r = changes.apply_plan(p["id"], True)
    assert r["verified"] and dl.mirror == {"en": 3, "tag": 2, "src": "00110000"}
    off = plan(dlinkchanges.plan_mirror("dlink-test", "Disable", None, []))
    assert changes.apply_plan(off["id"], True)["verified"] and dl.mirror["en"] == 0
    with pytest.raises(ValueError):
        dlinkchanges.plan_mirror("dlink-test", "Rx", 2, [2])


def test_only_five_cgis_can_be_posted_and_inputs_are_validated(dl):
    with webui.open_session("dlink-test") as s:
        for bad in ("login.cgi", "logout.cgi", "q_vlan_state.cgi", "reset.cgi", "q_vlan_rename.cgi", "../cgi/x"):
            with pytest.raises(ValueError, match="refusing"):
                s._post(bad, {})
        with pytest.raises(ValueError):
            s.write_vlan(11, "bad name!", [1], [])
        with pytest.raises(ValueError):
            s.write_vlan(11, "ok", [1], [1])
        with pytest.raises(ValueError):
            s.write_vlan(5000, "ok", [1], [])
        with pytest.raises(ValueError):
            s.write_pvids([1, 2, 3])
        with pytest.raises(ValueError):
            s.delete_vlan(1)
    assert dl.posts == []
    assert dlinkweb.WRITE_CGIS == {"q_vlan_add_edit.cgi", "q_vlan_del.cgi", "q_vlan_set_pvid.cgi", "set_port.cgi", "PortMirroring.cgi"}


def test_unverified_write_is_reported(dl, monkeypatch):
    p = plan(dlinkchanges.plan_port("dlink-test", 6, "Auto", True))
    monkeypatch.setattr(dlinkweb.DLinkSession, "write_port", lambda self, *a: None)  # the switch silently ignores the write
    r = changes.apply_plan(p["id"], True)
    assert r["verified"] is False and "warning" in r
