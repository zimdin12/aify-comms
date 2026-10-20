# S3: the bridge copy's other readers, before any no-copy cutover

Part of [the guided-install plan](../2026-10-20-guided-install.md). Rev 3 answers REVISE 03375b9b, item 4.

## The readers found so far

A positive list, not a census.
- Found by direct spellings: `AIFY_BRIDGE_DIR/<file>` in comms install.sh at 7c1f76da, and `BRIDGE_DIR` or
  `HERMES_STDIO_DIR` in aify-wrapper's templates at 5a080a1.
- A reader spelled any other way (a variable, a plugin manifest, the hermes plugin at install.sh:1146) is not on
  it. Such a reader keeps the copy declared until someone finds it.

| file in the copy | read by | client, mode, when | S3 disposition |
|---|---|---|---|
| `server.js` | MCP stdio entry (register-service-cli.mjs:61-62; install.sh `register_stdio_server`) | all clients, runtime | S4 (SSE) or retained file-set |
| `claude-channel.js` | channel MCP entry (register-service-cli.mjs:63; install.sh:1886,1891) | claude, runtime | moves into the aify-wrapper channel plugin. The comms run claim and wake stay comms code, reached over HTTP; that design gets its own page before code. The warning's trust policy stays S5's |
| `claude-stop-gate.js`, `agent-state-event.mjs` | Claude turn-end hook (install.sh:1697-1746, gate at :1701), always installed with an endpoint (:2021-2031) | claude, resident and managed, every turn end | decided here |
| `agent-state-event.mjs` | codex turn hooks (:1467-1474), hermes turn hooks (:1525-1540), the exit helper aify-runtime-exited.sh:19-22 called by every launcher (claude template :884, codex :715) | codex, hermes, all launchers at exit | decided here |
| `notify-check.js` | claude hook (:1760), codex hook (:1375), the plugin manifest | claude, codex, every tool call when opted in | decided here |
| `codex-hook-trust.mjs` | codex hook trust (:1522) | codex, install time | decided here |
| `doctor.js` | the `aify-comms doctor` verifier (:857-859, installed by `install_bridge_launcher` :1991), :2111 | host, on demand | decided here |
| `usage-preflight.js` | install time (:2097-2098) | install only | decided here |
| `hermes-daemon-cli.js`, `hermes-managed-host.js` | install.sh:2079-2080; hermes template :767-773 | hermes, managed and gateway | decided here |
| `claude-session-hook.js` | claude template :857-863 (SessionStart, UserPromptSubmit) | claude, every session | decided here |
| `agent-for-handle.mjs` | resume lookup: claude template :576, codex :538, hermes :531 | all, on resume | decided here |
| `reap-managed-claude.js` | claude template | claude, managed | decided here |

## Rule

- Each row gets a disposition before S4:
  - moved into an approved aify-wrapper or comms client artifact;
  - replaced by an HTTP call to the service;
  - or retained, with the copy declared for that client and mode.
- An UNKNOWN or deferred row keeps the copy declared.
- A future status or C7 migration counts only once it has landed. Today's hook writers are not assumed deleted.
- Operator-owned hooks are never removed to make no-copy pass.
- S4's cutover is gated on this table having no UNKNOWN row for the selected clients and modes.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| each moved row | the row's client and mode run with `$AIFY_HOME/mcp/stdio` renamed away, and its event still arrives (turn end, wake, resume, notice, doctor answer) | it needs the copy |
| retained rows | install-state lists the copy as declared for exactly the retained rows' clients | the copy is absent while a retained reader is selected |
