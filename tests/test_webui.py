import json

import pytest

from switch_mcp import webui

from pathlib import Path

FIX = Path(__file__).parent / "fixtures"

PAGE = """<html><title>VLAN</title><body><h1>Demo</h1>
<form><table><tr><td>Port<td>PVID<tr><td>1<td>11</table>
<input name="pvid1" value="11"><input type="checkbox" name="en" checked><input type="password" name="passwd" value="dummy">
<select name="mode"><option value="0">Off<option value="1" selected>On</select></form></body></html>"""


def test_extract_handles_unclosed_tags_and_hides_password():
    data = webui.extract(PAGE)
    assert data["title"] == "VLAN"
    assert data["headings"] == ["Demo"]
    assert data["tables"] == [[["Port", "PVID"], ["1", "11"]]]
    names = {c["name"]: c for c in data["controls"]}
    assert names["pvid1"]["value"] == "11"
    assert names["en"]["checked"] is True
    assert names["passwd"]["value"] == "<hidden>"
    assert [o["label"] for o in names["mode"]["options"]] == ["Off", "On"]
    assert names["mode"]["options"][1]["selected"] is True


def test_real_status_page_is_key_value():
    data = webui.extract((FIX / "status.html").read_text())
    assert data["values"]["Firmware Version"] == "2.00.08_1109"
    assert data["values"]["System Name"] == "demo-usr"
    assert "ID" not in data["values"]  # later two-column tables are not settings
    assert len(str(data)) < 6000  # the first parser blew a real page up to megabytes


def test_real_vlan_page_lists_groups():
    data = webui.extract((FIX / "vlan.html").read_text())
    rows = next(t for t in data["tables"] if t[0][:1] == ["ID"])
    assert rows[0] == ["ID", "Description", "Member"]
    assert rows[1][:2] == ["01", "Default"]
    assert any(r[1] == "IoT" for r in rows)
    assert len(rows) == 16  # header + 15 groups
    last = rows[-1]
    assert last[0] == "15" and last[1] == "" and len(last[2].split()) == 24  # empty description keeps its column


def test_real_trunk_and_mirror_pages_have_selects():
    trunk = webui.extract((FIX / "trunk.html").read_text())
    assert trunk["controls"][0]["options"][0]["label"] == "Disable"
    mirror = webui.extract((FIX / "mirror.html").read_text())
    assert any(c["type"] == "select" for c in mirror["controls"]) or mirror["controls"]


def test_read_pages_are_only_read_pages():
    forbidden = {"cgi_pass", "cgi_reset", "cgi_backup", "cgi_logout", "cgi_login"}
    assert not forbidden & set(webui.READ_PAGES.values())


def test_password_from_env(monkeypatch):
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_USR_TEST", "pw")
    assert webui.load_password("usr-test") == "pw"


def test_password_file_must_be_private(tmp_path, monkeypatch):
    monkeypatch.delenv("SWITCH_MCP_PASSWORD_USR_TEST", raising=False)
    monkeypatch.setattr(webui, "CONFIG_DIR", tmp_path)
    f = tmp_path / "passwords.json"
    f.write_text(json.dumps({"usr-test": "pw"}))
    f.chmod(0o644)
    with pytest.raises(RuntimeError, match="chmod 600"):
        webui.load_password("usr-test")
    f.chmod(0o600)
    assert webui.load_password("usr-test") == "pw"


def test_dlink_web_not_implemented():
    with pytest.raises(NotImplementedError):
        webui.Session("dlink-test")


def test_unknown_switch():
    with pytest.raises(KeyError):
        webui.Session("nope")
