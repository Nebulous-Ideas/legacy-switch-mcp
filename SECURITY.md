# Security

## Reporting a problem
Please report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on this repository
(https://github.com/Nebulous-Ideas/legacy-switch-mcp/security/advisories/new). Do not open a public issue for a security bug.
Include the version, what you did, and what you saw; never include real passwords, configuration files or switch addresses.
This is a volunteer project with no service-level agreement. We aim to acknowledge a report within 14 days. Fix timing is best effort and depends on
the report and on upstream releases (the MCP SDK, Python and the switches' own firmware); we make no commitment to a fix date.

## Sending a fix
Fixes are welcome. For a vulnerability that is not public yet, report it privately first (above); the advisory can host a private fix
that is published together with the advisory, so the problem is not disclosed before a release exists.
For anything already public or low risk (a vulnerable dependency, a hardening change, a documentation fix), open a pull request the normal way
(see `CONTRIBUTING.md`): branch from `main`, add a test where the change affects behaviour, and say in the description which problem it addresses.
Security-related PRs are looked at ahead of other work, as time allows, and an accepted fix ships in a new release.

## Threat model (what this tool is and is not)
**Design stance.** A switch like this is an inherently dangerous device: it speaks plain text and controls the physical layer-2 fabric of a network.
This tool is a way to manage that fabric (VLAN membership, per-port settings, trunks, mirroring and the switch's own management address).
It is not a gateway to a layer-3 environment: it does no routing, filtering or access control of its own, and the policy that matters
stays in the layer-3 device above the switch. Using it safely depends on isolation that you build around it: a management network the
switch is reachable on and nothing else is, protected uplinks, and a design in which a failure or a mistaken change fails closed
or fails in a way that does not stop the rest of the network. The tool helps by making every change a reviewed plan, refusing to apply when the
switch has changed, verifying by read-back and offering a rollback, but it cannot encrypt or authenticate the switch's traffic, and a change that cuts
the management path cannot be undone remotely. Keep out-of-band access and a known-good configuration.

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
