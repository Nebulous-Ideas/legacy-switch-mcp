# Changelog

Human-readable notes for each release. The same text appears on the
[Releases page](https://github.com/Nebulous-Ideas/legacy-switch-mcp/releases). Versions follow semantic versioning.
Each entry says whether the release fixes a publicly known vulnerability.

## Unreleased

## 0.1.2 - 2026-11-11
- A pre-release gate (development mode, warnings as errors, heavier property-test run) must pass before a release is published; it found and fixed an unclosed file handle in the stored-file checksum check.
- Hypothesis property tests for the parsers; fixed a crash on form controls without a `name` attribute.
- `CONTRIBUTING.md`, issue templates, and a ruff lint check in CI.
- Release files are signed with Sigstore (`*.sigstore.json` on the release) and the registry entry is published from GitHub Actions.
- Security: no publicly known vulnerabilities fixed.

## 0.1.1 - 2026-11-10
- Project links on the PyPI page, a smaller source distribution, and Dependabot no longer proposes `mcp` major-version bumps (2.x support is planned).
- Security: no publicly known vulnerabilities fixed.

## 0.1.0 - 2026-11-10
- First public release: plan-then-apply management for D-Link DGS-1100 and USRobotics 7724 smart switches over MCP.
- Discovery, reads, and VLAN, port, mirror and trunk changes with read-back verification, protected ports, an audit log and rollback.
- Hardware-proven on two models; see the README status tables.
- Security: first release; no vulnerabilities fixed.
