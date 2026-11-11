import json
import os

import pytest
from hypothesis import settings

from legacy_switch_mcp import webui

# Property tests: a quick default for everyday runs, and a much heavier profile the release workflow selects (HYPOTHESIS_PROFILE=release).
settings.register_profile("default", max_examples=200, deadline=None)
settings.register_profile("release", max_examples=5000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


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
