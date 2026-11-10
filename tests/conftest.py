import json

import pytest

from legacy_switch_mcp import webui


@pytest.fixture(autouse=True)
def switch_registry(tmp_path, monkeypatch):
    """Every test sees the same two documentation-range switches, never the developer's real config."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "switches.json").write_text(json.dumps({
        "usr-test": {"host": "192.0.2.3", "vendor": "usrobotics"},
        "dlink-test": {"host": "198.51.100.3", "vendor": "dlink"},
    }))
    monkeypatch.setattr(webui, "CONFIG_DIR", cfg)
