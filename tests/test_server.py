import pytest

from legacy_switch_mcp import server


def test_bad_page_is_refused_before_any_login(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not log in for an invalid page")
    monkeypatch.setattr(server.webui, "Session", boom)
    with pytest.raises(ValueError):
        server.read_page("usr-test", "cgi_reset")


def test_probe_reports_no_reply(monkeypatch):
    monkeypatch.setattr(server.discovery, "probe", lambda host: None)
    assert "error" in server.probe("192.0.2.99")


# ---- which tools are registered (every registered tool costs the session tokens on every request) -----------------------------
import asyncio
import json

from mcp.server.mcpserver import MCPServer

BOTH = {"a": {"host": "192.0.2.1", "vendor": "usrobotics"}, "b": {"host": "192.0.2.2", "vendor": "dlink"}}


def names_for(env, switches=BOTH):
    s = MCPServer("t")
    server.register_tools(s, server.selected_toolsets(env, switches))
    return {t.name for t in asyncio.run(s.list_tools())}


def test_default_registers_only_what_can_be_used():
    n = names_for({})
    assert {"get_state", "backup_config", "plan_vlan_group", "dlink_plan_trunk", "apply_plan"} <= n
    assert not n & {"plan_password", "plan_ip", "plan_reboot", "plan_factory_reset", "plan_restore_config"}       # maintenance is off
    assert len(n) == 10 + 3 + 5 + 9 + 1


def test_maintenance_tools_appear_only_when_enabled():
    assert {"plan_password", "plan_reboot"} <= names_for({"SWITCH_MCP_ENABLE_MAINTENANCE": "1"})


def test_a_dlink_only_setup_skips_the_usr_plans():
    n = names_for({}, {"b": BOTH["b"]})
    assert "dlink_plan_vlan" in n and not n & {"plan_vlan_group", "plan_port", "plan_trunk"}
    assert "usr" not in server.selected_toolsets({}, {"b": BOTH["b"]})


def test_a_look_only_session_has_no_plan_or_apply_tools():
    n = names_for({"SWITCH_MCP_TOOLSETS": "read"})
    assert "get_state" in n and "apply_plan" not in n and not [x for x in n if "plan" in x.replace("list_snapshots", "")] and len(n) == 10


def test_toolsets_can_be_chosen_explicitly_and_typos_are_refused():
    assert {"plan_reboot", "dlink_plan_save", "apply_plan"} <= names_for({"SWITCH_MCP_TOOLSETS": "read,dlink,maintenance"})
    assert len(names_for({"SWITCH_MCP_TOOLSETS": "all"})) == 33
    with pytest.raises(ValueError, match="unknown SWITCH_MCP_TOOLSETS"):
        server.selected_toolsets({"SWITCH_MCP_TOOLSETS": "reed"}, BOTH)


def test_tool_descriptions_stay_short():
    s = MCPServer("t")
    server.register_tools(s, set(server.TOOLSETS))
    tools = asyncio.run(s.list_tools())
    assert max(len(t.description) for t in tools) < 330
    assert sum(len(t.description) + len(str(t.input_schema)) for t in tools) < 14000


def test_schema_titles_are_stripped_but_arguments_are_still_validated():
    s = MCPServer("t")
    server.register_tools(s, {"read"})
    server.slim_schemas(s)
    tools = {t.name: t for t in asyncio.run(s.list_tools())}
    assert "title" not in json.dumps(tools["discover"].input_schema)
    assert set(tools["discover"].input_schema["properties"]) == {"interface", "wait_seconds"}
    assert tools["discover"].input_schema["required"] == ["interface"]
