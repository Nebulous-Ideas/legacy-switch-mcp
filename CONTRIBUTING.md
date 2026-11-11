# Contributing

Thanks for helping. This project manages real network switches, so changes are small, tested and reversible.
Please also read `AGENTS.md` (the working rules, which apply to people and AI agents alike) and `SECURITY.md`.

## Ways to contribute
- **Bugs and ideas:** open a GitHub issue. Say which switch model and firmware, what you did and what you saw.
  Never include real passwords, configuration files, serial numbers or your switch addresses; use `192.0.2.x` examples.
- **Security problems:** do not open a public issue; use the private report described in `SECURITY.md`.
- **A new switch model or vendor:** follow `docs/ADDING-SWITCHES.md`. Wire formats go in `docs/PROTOCOL.md`.
- **Code and docs:** open a pull request against `main`.

## Pull requests
1. Fork or branch from `main`. `main` is protected: changes land through a PR with passing checks.
2. Set up and test:
   ```bash
   uv sync --extra dev
   uv run pytest          # all tests, ~2 s, fake switches only (no hardware)
   uv run ruff check .    # lint
   ```
   CI runs the same on Python 3.11, 3.12 and 3.13 plus a dependency audit; do not use newer-only features.
3. Keep the change focused and explain why, not just what.

## Rules that decide whether a change is accepted
- **Test policy:** every new feature and every bug fix comes with a test. For a bug, add the failing test first. Tests run against the fake
  switches in `tests/` (`fakeswitch.py`, `fakedlink.py`), never a real device. When real hardware disagrees with a fake, fix the fake too.
- **No new write path without a fake-switch test, and never outside the allow-lists** in `dlinkweb.py` and `webui.py`.
  A write is called "proven" only after a round trip on real hardware; otherwise the README says "fake-tested only".
- **Nothing real in the repo:** use RFC 5737 addresses (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`) and `00-00-5E-00-53-xx` MACs.
  No real hostnames, serials, passwords, tokens or personal names, in code, fixtures, docs, commits, issues or PRs.
- **Keep the tool list small:** every registered MCP tool costs every user tokens on every request. Short descriptions, only usable tools.
- **Dependencies** change through `uv lock` (never hand-edit `uv.lock`); GitHub Actions are pinned to commit SHAs.
- Docs change in the same PR as the code they describe.

## License
By contributing you agree your work is released under the MIT License (see `LICENSE`).
