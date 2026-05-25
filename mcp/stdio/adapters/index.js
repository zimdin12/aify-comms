import { ClaudeAdapter } from "./claude.js";
import { CodexAdapter } from "./codex.js";
import { HermesAdapter } from "./hermes.js";
import { PiAdapter } from "./pi.js";
import { OpencodeAdapter } from "./opencode.js";

const REGISTRY = new Map([
  ["claude-code", ClaudeAdapter],
  ["codex", CodexAdapter],
  ["hermes", HermesAdapter],
  ["pi", PiAdapter],
  ["opencode", OpencodeAdapter],
]);

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
  const cls = REGISTRY.get(canonical);
  if (!cls) {
    throw new Error(`Unknown runtime "${name}". Known: ${[...REGISTRY.keys()].join(", ")}`);
  }
  return new cls();
}

export function supportedRuntimes() {
  return [...REGISTRY.keys()];
}
