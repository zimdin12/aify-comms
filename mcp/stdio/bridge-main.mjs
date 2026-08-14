// Bringing the bridge up: connect the MCP transport, arm the harness-death guard, auto-register.
// Extracted from server.js in v0.5.4.
//
// `StdioServerTransport` IS A PARAMETER, NOT AN IMPORT, and that is load-bearing rather than style.
// server.js pulls the MCP SDK in through a DYNAMIC import placed deliberately AFTER its
// `AIFY_BRIDGE_DISABLED` early exit, so an RPC child bridge never loads the SDK at all. A static
// import here would be evaluated when server.js imports this module — before that exit — and would
// quietly undo it. `tests/rpc-child-bridge-disabled.test.js` is what would notice.
//
// The other four are server.js's own: the McpServer instance, the pid captured at startup, the
// shutdown chain, and the dispatch starter handed to auto-registration.

import { IS_REMOTE, SERVER_URL } from "./aify-service-endpoint.mjs";
import { makeAutoRegister } from "./auto-registration.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { MESSAGES_DIR } from "./local-store.mjs";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { AIFY_VERSION } from "./version.js";

export async function main({
  ORIGINAL_PARENT_PID,
  StdioServerTransport,
  ensureDispatchLoop,
  server,
  shutdownWithStatus,
}) {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`aify-comms-mcp v${AIFY_VERSION} running on stdio`);
  console.error(`Mode: ${IS_REMOTE ? "REMOTE (" + SERVER_URL + ")" : "LOCAL (" + MESSAGES_DIR + ")"}`);
  console.error(`Working dir: ${DEFAULT_CWD}`);
  // "Never leave a bridge child behind." For an MCP-CHILD bridge (loaded by a
  // claude/codex/hermes harness), poll the controlling harness — the parent pid
  // captured at startup. When it dies, shut down gracefully (same teardown as
  // SIGTERM) instead of lingering as an orphan the server has to reap. (Found 6
  // of these server.js children reparented to the WSL init relay for ~10h.) We
  // poll the ORIGINAL parent pid, so reparenting (ppid -> init/Relay after the
  // harness dies) doesn't hide the death. stdin-EOF would be cleaner, but the MCP
  // SDK transport reads stdin via 'data' only and never propagates EOF (verified).
  // EXCLUDED for the environment bridge: top-level process, its own lifecycle.
  if (!IS_ENVIRONMENT_BRIDGE && ORIGINAL_PARENT_PID > 1) {
    let parentMisses = 0;
    const harnessGuard = setInterval(() => {
      let alive = true;
      try { process.kill(ORIGINAL_PARENT_PID, 0); } catch (e) { alive = (e && e.code === "EPERM"); }
      if (alive) { parentMisses = 0; return; }
      if (++parentMisses < 2) return; // tolerate one transient miss (~3s)
      clearInterval(harnessGuard);
      try { console.error(`[aify] controlling harness pid=${ORIGINAL_PARENT_PID} gone; MCP-child bridge shutting down`); } catch { /* best effort */ }
      shutdownWithStatus(0); // idempotent via shutdownStarted; same teardown as SIGTERM
    }, 3000);
    if (typeof harnessGuard.unref === "function") harnessGuard.unref();
  }
  // Codex app-server waits for its MCP servers to finish initializing while
  // registration discovers the live thread through that same app-server.
  // Do not deadlock MCP startup on the discovery round-trip.
  makeAutoRegister({ ensureDispatchLoop })().catch((err) => {
    console.error(`[aify] auto-registration failed: ${err?.message || err}`);
  });
}
