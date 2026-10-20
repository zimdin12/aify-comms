# S4: comms over SSE on agent hosts, then the no-copy cutover

Part of [the guided-install plan](../2026-10-20-guided-install.md). Needs S3.

- **Mirror the five tools** the SSE surface lacks only by omission: `comms_agent_info`, `comms_contracts`,
  `comms_status`, `comms_describe`, `comms_unsend` (TARGET_ARCHITECTURE gap 1). The eight principled absences stay
  declared in `transport-parity.test.js`.
- **comms' spec** moves `mcp` to `{http: {path: "/mcp/sse"}}` and drops the bridge-copy artifact once S3 has
  removed its other consumers.
- **The installer** makes `copy_bridge_to_native_dir` (install.sh:1963, unconditional today) conditional on the spec
  declaring the artifact. That is new code, not a flag already honoured.
- **A host that still needs a stdio-only tool** keeps the copy as an explicit choice, and install-state reports it.

## Witnesses

| obligation | witness | RED when |
|---|---|---|
| parity | `transport-parity.test.js` lists only the eight principled absences | one of the five is still missing |
| no copy | a client-only install from the S2 chain leaves no `~/.aify-comms`, and comms' tools answer over SSE | the copy is created, or a tool fails |
| explicit fallback | `--keep-bridge-copy` creates it, and install-state reports it | it is created silently |
