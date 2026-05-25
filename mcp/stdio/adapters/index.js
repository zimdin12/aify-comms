import { ClaudeAdapter } from "./claude.js";
import { CodexAdapter } from "./codex.js";
import { HermesAdapter } from "./hermes.js";
import { PiAdapter } from "./pi.js";
import { OpencodeAdapter } from "./opencode.js";

// REGISTRY is built lazily on first call to adapterFor() to break the
// module-load cycle introduced by Plan 3 controllers:
//   adapters/index.js -> adapters/X.js -> controllers/X.js ->
//     runtimes-helpers.js -> runtimes.js -> adapters/index.js
// During that re-entry, the partial namespace of adapters/index.js does
// not yet have adapter classes bound (TDZ on the inline Map literal).
// Deferring map construction to first use sidesteps the TDZ: by then,
// all top-level evaluation has completed and every adapter class binding
// is live.
let _REGISTRY = null;
function getRegistry() {
  if (!_REGISTRY) {
    _REGISTRY = new Map([
      ["claude-code", ClaudeAdapter],
      ["codex", CodexAdapter],
      ["hermes", HermesAdapter],
      ["pi", PiAdapter],
      ["opencode", OpencodeAdapter],
    ]);
  }
  return _REGISTRY;
}

const ALIASES = new Map([
  ["claude", "claude-code"],
  ["claude_code", "claude-code"],
  ["hermes-agent", "hermes"],
  ["hermes_agent", "hermes"],
  ["oh-my-pi", "pi"],
  ["oh_my_pi", "pi"],
  ["omp", "pi"],
  ["pi-agent", "pi"],
  ["pi_agent", "pi"],
]);

export function adapterFor(name) {
  const key = String(name == null ? "" : name).trim().toLowerCase();
  const canonical = ALIASES.get(key) || key;
  const registry = getRegistry();
  const cls = registry.get(canonical);
  if (!cls) {
    throw new Error(`Unknown runtime "${name}". Known: ${[...registry.keys()].join(", ")}`);
  }
  return new cls();
}

export function supportedRuntimes() {
  return [...getRegistry().keys()];
}
