# Working on legacy-switch-mcp (people and agents)

MCP server for old smart switches (D-Link DGS-1100, USRobotics 7724). Changes are **plan, then apply**; nothing is written to a switch otherwise.
Layout: `src/legacy_switch_mcp/` (server, per-vendor web sessions, change planners, safety, maintenance), `tests/` (fake switches, no hardware), `scripts/` (hardware-only), `docs/`.

## Commands
```bash
uv sync --extra dev
uv run pytest                      # 112 tests, ~1 s, fake switches only
```
CI runs the same on Python 3.11, 3.12 and 3.13: do not use newer-only features.

## Rules that matter most
- **Never post to a switch outside the allow-lists** in `dlinkweb.py` / `webui.py`, and never add a write path without a fake-switch test first. A write is "proven" only after a round trip on an unused, unprotected port; label it so in the README status table with the date, and say "fake-tested only" otherwise.
- **Do not run anything in `scripts/` or point tools at a real switch** unless the maintainer asked for it in this session. Maintenance stays off (`SWITCH_MCP_ENABLE_MAINTENANCE` unset) by default.
- **Nothing real in the repo**: fixtures, docs, tests, commit messages, issues and PRs use RFC 5737 addresses (192.0.2.x, 198.51.100.x, 203.0.113.x) and `00-00-5E-00-53-xx` MACs. No real hostnames, serials, passwords, tokens or personal names. Config and state live in `~/.config/switch-mcp` and `~/.local/state/switch-mcp`, never here.
- **Redact by value, not by key name.** When showing config or command output, mask anything shaped like a secret (long random strings, `user:key@host` URLs, `Authorization` headers) instead of trusting that a field is named "password". If a secret is exposed, say so and treat it as leaked.
- **Credentials are the maintainer's to create.** Do not mint, rotate or store tokens or keys yourself; give the exact command instead. Never put credentials in git config, remotes or files in this repo.

## Working method
- **Verify, don't trust.** Run the tests, read the actual diff, and check claimed results yourself before saying something is done. A confident summary is not evidence; report failures and skipped steps plainly, with the output.
- **Branch, don't commit to `main`.** Work on a branch, run `uv run pytest` before pushing, and open a PR. Commits keep the existing author identity; agent-assisted commits end with a `Co-Authored-By:` trailer naming the agent.
- **Keep the MCP footprint small.** Every registered tool adds its description to every request of a session. Keep descriptions short, register only usable tools, and re-measure the token table in the README if the tool set changes.
- **Reproduce before fixing.** Add a failing fake-switch test for a bug first. The fakes only know what has been learned; when hardware disagrees with a fake, fix the fake too so the regression fails a test (see "Found on hardware" in the README).
- **Docs follow code.** `docs/ADDING-SWITCHES.md` is the process for new models and vendors; `docs/PROTOCOL.md` holds wire formats. Update them in the same change.

## Dependencies
Dependabot opens weekly PRs (`uv`, `github-actions`). A bump is acceptable when CI is green; read the changelog for `mcp` (the SDK defines the tool schema) and re-run `tests/test_server.py` expectations for the registered tool set.
