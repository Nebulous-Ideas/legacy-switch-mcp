import pytest

from fakeswitch import FakeSwitch
from switch_mcp import changes, webui


@pytest.fixture
def sw(monkeypatch, tmp_path):
    fake = FakeSwitch(up_ports=(1, 2, 3, 4))
    monkeypatch.setattr(webui, "TRANSPORT", fake.transport())
    monkeypatch.setattr(changes, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_USR_TEST", "pw")
    changes.PLANS.clear()
    return fake


def test_parsers_read_the_fake_like_the_real_thing(sw):
    with webui.Session("usr-test") as s:
        st = s.state()
    assert len(st["vlan"]) == 15 and st["vlan"][0]["description"] == "Default" and len(st["vlan"][0]["members"]) == 24
    assert st["ports"][0] == {"port": 1, "speed_set": "Auto", "link": "1G Full", "flow_set": "Disable", "flow_status": "Off", "qos": "Normal"}
    assert st["mirror"] == {"mode": "Disable", "sniffer_port": None, "source_port": None}


def test_real_pages_parse_with_the_same_parsers():
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures"
    vlan = webui.parse_vlan_groups(webui.extract((fx / "vlan.html").read_text()))
    assert len(vlan) == 15 and vlan[10]["description"] == "IoT" and len(vlan[10]["members"]) == 24
    ports = webui.parse_ports(webui.extract((fx / "port.html").read_text()))
    assert len(ports) == 24 and ports[0]["link"] == "100M Full" and ports[4]["link"] == "Down"
    assert webui.parse_mirror(webui.extract((fx / "mirror.html").read_text()))["mode"] == "Disable"


def test_plan_writes_nothing(sw):
    plan = changes.plan_vlan_group("usr-test", 11, [1, 2, 5], "IoT")
    assert plan["status"] == "planned" and sw.posts == []
    assert any("remove ports" in line for line in plan["summary"])
    assert plan["risks"] == []  # every other group still holds all ports, so nobody loses a path


def test_isolating_connected_ports_is_flagged(sw):
    for g in sw.groups:  # make group 11 the only group that joins ports 1-4
        g["members"] = [24]
    sw.groups[10]["members"] = [1, 2, 3, 4, 24]
    plan = changes.plan_vlan_group("usr-test", 11, [1, 2, 24])
    assert any("could no longer talk" in r and "03<->04" in r for r in plan["risks"])


def test_apply_needs_confirm(sw):
    plan = changes.plan_vlan_group("usr-test", 11, [1, 2, 5])
    with pytest.raises(ValueError, match="confirm"):
        changes.apply_plan(plan["id"], False)
    assert sw.posts == []


def test_apply_vlan_verifies_snapshots_and_rolls_back(sw, tmp_path):
    plan = changes.plan_vlan_group("usr-test", 11, [1, 2, 5], "IoT")
    result = changes.apply_plan(plan["id"], True)
    assert result["applied"] and result["verified"]
    assert sw.groups[10]["members"] == [1, 2, 5]
    assert sw.posts[0][0] == "cgi_setvlan=10" and sw.posts[0][1]["vcb"] == ["00", "01", "04"]
    assert (tmp_path / "state").exists() and len(list((tmp_path / "state").glob("*.json"))) == 1
    assert sw.logouts >= 1
    back = changes.apply_plan(result["rollback_plan_id"], True)
    assert back["verified"] and sw.groups[10]["members"] == list(range(1, 25))


def test_stale_plan_is_refused(sw):
    plan = changes.plan_vlan_group("usr-test", 11, [1, 2, 5])
    sw.groups[10]["members"] = [1, 2, 3]  # someone changed it meanwhile
    with pytest.raises(RuntimeError, match="changed since"):
        changes.apply_plan(plan["id"], True)
    assert sw.posts == []


def test_plan_cannot_be_applied_twice(sw):
    plan = changes.plan_vlan_group("usr-test", 11, [1, 2, 5])
    changes.apply_plan(plan["id"], True)
    with pytest.raises(ValueError, match="applied"):
        changes.apply_plan(plan["id"], True)


def test_port_plan_warns_on_forced_speed_and_applies(sw):
    plan = changes.plan_port("usr-test", 1, "100M Full", False, "Normal")
    assert any("forced speed" in r for r in plan["risks"])
    assert changes.apply_plan(plan["id"], True)["verified"]
    assert sw.ports[1]["speed"] == "100M Full"


def test_port_validation(sw):
    with pytest.raises(ValueError):
        changes.plan_port("usr-test", 1, "Auto", False, "Normal")  # nothing to change
    with webui.Session("usr-test") as s:
        with pytest.raises(ValueError):
            s.write_port(1, "100M Full", True, "Normal")  # flow control needs Auto
        with pytest.raises(ValueError):
            s.write_port(25, "Auto", False, "Normal")


def test_mirror_plan_and_apply(sw):
    plan = changes.plan_mirror("usr-test", "Both", 2, 3)
    assert any("sniffer port 02 is up" in r for r in plan["risks"])
    assert changes.apply_plan(plan["id"], True)["verified"]
    assert sw.mirror == {"mode": "Both", "sniffer": 2, "source": 3}
    off = changes.plan_mirror("usr-test", "Disable", None, None)
    assert changes.apply_plan(off["id"], True)["verified"] and sw.mirror["mode"] == "Disable"


def test_only_three_forms_can_be_posted(sw):
    with webui.Session("usr-test") as s:
        for bad in ("cgi_pass", "cgi_reset", "cgi_sys", "cgi_backup", "cgi_trunk", "cgi_setvlan=abc"):
            with pytest.raises(ValueError, match="refusing"):
                s._post(bad, {})
    assert sw.posts == []


def test_vlan_validation(sw):
    with webui.Session("usr-test") as s:
        with pytest.raises(ValueError):
            s.write_vlan(11, "IoT", [])
        with pytest.raises(ValueError):
            s.write_vlan(11, "has;semicolon", [1])
        with pytest.raises(ValueError):
            s.write_vlan(16, "x", [1])


def test_clearing_a_description_sends_a_space(sw):
    sw.groups[10]["desc"] = "IoT"
    plan = changes.plan_vlan_group("usr-test", 11, list(range(1, 25)), "")
    result = changes.apply_plan(plan["id"], True)
    assert result["verified"] and sw.groups[10]["desc"] == ""
    assert sw.posts[-1][1]["desc"] == " "
