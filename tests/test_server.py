import pytest

from switch_mcp import server


def test_bad_page_is_refused_before_any_login(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not log in for an invalid page")
    monkeypatch.setattr(server.webui, "Session", boom)
    with pytest.raises(ValueError):
        server.read_page("usr-test", "cgi_reset")


def test_probe_reports_no_reply(monkeypatch):
    monkeypatch.setattr(server.discovery, "probe", lambda host: None)
    assert "error" in server.probe("192.0.2.99")
