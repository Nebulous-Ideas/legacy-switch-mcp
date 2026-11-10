from pathlib import Path

import httpx
import pytest

from legacy_switch_mcp import dlinkweb, server, webui

FIX = Path(__file__).parent / "fixtures"


def _files():
    names = {"system": "Switch", "vlan": "QVLAN", "port": "Port", "mirror": "Mirror", "pvlan": "PVLAN", "trunk": "trunking"}
    return {k: dlinkweb.parse_js_vars((FIX / f"dlink_{v}.js").read_text()) for k, v in names.items()}


def test_parse_js_vars_handles_real_data():
    q = dlinkweb.parse_js_vars((FIX / "dlink_QVLAN.js").read_text())
    assert q["ds_QVEnable"] == 1
    assert q["ds_PVID"] == [1] * 8
    assert q["ds_QVLANList"] == [[1, "", "UUUUUUUU"]]
    assert q["ds_OUIList"] == []


def test_parse_js_vars_keeps_unparsable_text():
    assert dlinkweb.parse_js_vars("var a=foo(1);\nvar b=[1,2]")["a"] == "foo(1)"
    assert dlinkweb.parse_js_vars("var a=foo(1);\nvar b=[1,2]")["b"] == [1, 2]


def test_build_state_from_real_files():
    st = dlinkweb.build_state(_files())
    assert st["system"]["name"] == "demo-dlk" and st["system"]["ip"] == "198.51.100.3"
    assert [p["link"] for p in st["ports"]] == ["1000M Full"] * 5 + ["Down"] * 3
    assert st["ports"][0]["speed_set"] == "Auto" and st["ports"][0]["flow_control"] is False and st["ports"][0]["pvid"] == 1
    assert st["vlan"]["enabled_802_1q"] is True
    assert st["vlan"]["vlans"][0]["untagged"] == list(range(1, 9)) and st["vlan"]["vlans"][0]["tagged"] == []
    assert st["mirror"] == {"enabled": False, "mode": "Disable", "sniffer_port": None, "source_ports": []}
    assert st["trunk"]["enabled"] is False


def _handler(log):
    def handle(request: httpx.Request) -> httpx.Response:
        log.append((request.method, request.url.path, request.headers.get("cookie")))
        if request.url.path == "/cgi/login.cgi":
            return httpx.Response(200, text="<script>\ndocument.cookie='SessID=3;path=/';\ndocument.cookie='Gambit=tok;path=/';\n</script>")
        if request.url.path == "/cgi/logout.cgi":
            return httpx.Response(200, text="bye")
        name = request.url.path.rsplit("/", 1)[-1]
        f = FIX / f"dlink_{name[:-3]}.js"
        return httpx.Response(200, text=f.read_text()) if f.exists() else httpx.Response(404)
    return handle


def test_session_logs_in_sends_cookies_and_logs_out(monkeypatch):
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_DLINK_TEST", "pw")
    log = []
    with dlinkweb.DLinkSession("dlink-test", transport=httpx.MockTransport(_handler(log))) as s:
        assert s.state()["system"]["name"] == "demo-dlk"
    assert log[0][:2] == ("POST", "/cgi/login.cgi")
    assert all("Gambit=tok" in c for _, p, c in log[1:-1])
    assert log[-1][:2] == ("POST", "/cgi/logout.cgi")


def test_bad_login_raises_and_releases_the_lock(monkeypatch):
    monkeypatch.setenv("SWITCH_MCP_PASSWORD_DLINK_TEST", "pw")
    bad = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>login failed</html>"))
    with pytest.raises(PermissionError):
        with dlinkweb.DLinkSession("dlink-test", transport=bad):
            pass
    assert webui._LOCK.acquire(blocking=False)  # would hang forever if the failed login leaked the lock
    webui._LOCK.release()


def test_only_listed_data_files_are_readable():
    assert set(dlinkweb.DATA_FILES.values()) == {"Switch.js", "QVLAN.js", "Port.js", "Mirror.js", "PVLAN.js", "MgtVLAN.js", "trunking.js"}


def test_server_refuses_bad_dlink_page_before_login(monkeypatch):
    monkeypatch.setattr(webui, "open_session", lambda n: (_ for _ in ()).throw(AssertionError("must not log in")))
    with pytest.raises(ValueError):
        server.read_page("dlink-test", "cgi/logout.cgi")
