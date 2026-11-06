# Issues opened after the R2 freeze

The [issue map](2026-11-04-issue-map.md) is frozen at the 109 issues the owners approved.
Issues opened later are placed here, so the approved map and its hashes stay unchanged.
The owner confirms the placement when picking an issue up.

| Issue | Group | Placement and what must not regress |
|---|---|---|
| [aify-comms#55](https://github.com/zimdin12/aify-comms/issues/55) | G0, design first | In-session register for an unbound launcher session, lost when tools moved to `/mcp/http` in 0.9.11. Needs comms (bind the MCP session on an explicit register), wrapper and env (lifetime record, lease and hook identity follow the binding). Keep: the model never invents an identity; a bound connection cannot rebind; a live holder is replaced only on request. |
| [aify-comms#56](https://github.com/zimdin12/aify-comms/issues/56) | G0 | Dashboard resume command drops `--dangerously-skip-permissions` so the launcher's own default applies. Keep the hermes and codex commands unchanged. |
| [aify-wrapper#27](https://github.com/zimdin12/aify-wrapper/issues/27) | G0, wrapper | `claude-aify --aify-agent <id>` resumes the agent's pinned session. Keep: launches that must start fresh (managed spawns from a definition, handoff compaction, an explicit new session) still start fresh; a transcript missing on this host falls back with a visible line. Check every caller that passes `--aify-agent` before changing the default. |
