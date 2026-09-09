// The bridge's release version — ONE declaration, imported by every MCP handshake.
//
// Until 2026-08-03 the string "4.0.0" was hand-copied into seven places (server.js,
// claude-channel.js, codex-session.js, hermes-session.js, runtimes-codex.js twice,
// codex-legacy-controller.js) plus package.json. It tracked nothing: the project shipped
// v0.1, v0.1.1 and v0.1.2 while every handshake kept announcing 4.0.0, and no single edit
// could have changed that because there was no single place to edit.
//
// Why a literal and not a read of package.json or the repo-root VERSION file: install.sh
// copies ONLY mcp/stdio into ~/.aify-comms, so the repo root does not exist at runtime, and
// the bridge is on a latency budget that this repo has already been bitten by (the native
// copy exists because a ~5s load blew hermes' 0.75s MCP-discovery window). A constant costs
// nothing to import. `tests/version-consistency.test.js` is what keeps it honest — it fails
// the suite if this drifts from VERSION or package.json, so the duplication is enforced
// rather than merely intended.
export const AIFY_VERSION = "0.6.3";
