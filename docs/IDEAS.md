# Ideas borrowed, and an umbrella that is only an idea

## What came from other projects in this space
Other MCP servers for gear with no vendor API (TP-Link Easy Smart, Netgear Smart Managed Plus, SSH to older CLI switches, UniFi controllers)
have settled on a few habits. This project adopted the ones that fit two port-based/EasySmart switches:

| Habit seen elsewhere | Here |
|---|---|
| Dry run before every write | `plan_*` tools: nothing is written until `apply_plan(confirm=true)` |
| Protected ports | `protected_ports` per switch in `switches.json`; plans touching them are refused unless `allow_protected=true` |
| Lockout-aware failure handling | the rollback plan is saved to disk BEFORE the write; if contact is lost afterwards, `apply_plan` reports it and the plan can be applied from a new session |
| Session-eviction caveat | one web session at a time, always logged out; documented in the README |
| JSONL audit log | `audit.jsonl` in the state directory: plans, refusals, applies, backups (never credentials) |
| Documentation output for network gear | `describe_switch` returns markdown for a wiki page |
| Backups before changes | a snapshot before every apply, plus `backup_state` and `list_snapshots` |

## The umbrella idea (not built: no other hardware here to test against)
The pieces that do not care about the vendor are already separate modules: the plan store and rollback (`changes.py`), the audit log and protected-port
guard (`safety.py`), the reports (`reports.py`). A multi-vendor "legacy switch" server would add one driver per family behind a small interface:

```
discover(interface) -> list[SwitchInfo]
state() -> dict                      # parsed, vendor-neutral where possible
plan_* / write_*                     # the few forms a driver may post
current(op, state, params), target(op, params), inverse(op, params, before)
```

`dlinkchanges.py` already has `current`/`target`/`inverse`/`write` in exactly that shape, so a TP-Link Easy Smart or Netgear driver (both have public
protocol write-ups) could be added without touching the core. Without the hardware to prove writes, that would be guesswork, so it stays a note.
