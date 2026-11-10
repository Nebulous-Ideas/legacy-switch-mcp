# Security

## Reporting a problem
Please report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on this repository
(https://github.com/Nebulous-Ideas/legacy-switch-mcp/security/advisories/new). Do not open a public issue for a security bug.
Include the version, what you did, and what you saw; never include real passwords, configuration files or switch addresses.
This is a volunteer project: expect an acknowledgement within a few days, not a service-level agreement.

## Threat model (what this tool is and is not)
- It manages old "smart" switches whose web interfaces speak **plain HTTP** with a simple login. Anyone who can see that traffic can read it.
  Run it only on a **trusted management network** (a management VLAN or a direct link), never across the internet.
- It does what an administrator can do on the switch's own web pages, through an allow-list of reads and writes. It is not a sandbox
  for an untrusted agent: connect it only to switches you are prepared to have changed, list your uplinks in `protected_ports`,
  and keep maintenance operations (password, IP, reboot, reset, restore) off unless you need them.
- Passwords are never tool arguments and are never returned. They come from environment variables or a `passwords.json` file with mode 0600.
  Downloaded switch configuration files can contain the admin password in clear text; they are stored mode 0600 and never returned.

## Supply chain
- Dependencies are locked with hashes (`uv.lock`); CI installs with `--frozen`, runs `pip-audit` against the locked set, and CodeQL and OpenSSF Scorecard run on the repository.
- Dependabot proposes updates weekly. Major versions of the `mcp` SDK are migrated deliberately, not by a bot.
- Releases are published to PyPI by GitHub Actions through trusted publishing (no stored token) and carry provenance attestations;
  each GitHub release has a CycloneDX SBOM attached (`sbom.cdx.json`).
  Each release also carries the built files with keyless Sigstore signatures (`*.sigstore.json`); verify with
  `sigstore verify github --cert-identity-repo Nebulous-Ideas/legacy-switch-mcp <file>` (from the release assets). Signing starts with the first release after this was added.
- GitHub Actions are pinned to commit SHAs.

## Supported versions
Only the latest release receives fixes.
