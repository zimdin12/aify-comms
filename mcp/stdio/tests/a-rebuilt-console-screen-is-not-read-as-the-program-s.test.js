// A console screen the service rebuilt from its stored tail is not read as what the program shows.
//
// After a service restart an idle TUI's console is fragments painted over a blank grid until the
// program clears its screen: measured 2026-09-16 on three running consoles, whose text overlapped
// (`ia-hHermes (all four lanes):agraftrisnenabledains`). The service now marks such a screen
// `reconstructed`. The agent reading it through `comms_console_tail` is told. (`context-window`, which
// does not parse a figure out of such a screen, moved to `aify-env doctor` in 0.9.7 with its tests.)

import assert from "node:assert/strict";
import test from "node:test";

process.env.AIFY_SERVER_URL = process.env.AIFY_SERVER_URL || "http://127.0.0.2:1";
process.env.CLAUDE_MCP_SERVER_URL = process.env.AIFY_SERVER_URL;
process.env.AIFY_AGENT_ID = process.env.AIFY_AGENT_ID || "test-reader";

const { commsConsoleTailHandler } = await import("../console-tools.mjs");

const FULL = "  │ 922.4k/900k │ [██████████] 100% │ 3m 39s";
const live = (extra) => ({ ok: true, live: true, terminalId: "term_1", status: "attached", lines: 1, output: FULL, ...extra });

async function tail(response) {
  const result = await commsConsoleTailHandler({ agentId: "a", lines: 5 }, { httpCall: async () => response });
  return result.content[0].text;
}

test("comms_console_tail says a rebuilt screen may not be what the program shows", async () => {
  const text = await tail(live({ reconstructed: true }));
  assert.match(text, /rebuilt from the stored log/i);
  assert.ok(text.includes(FULL), "the screen itself must still be shown");
});

test("CONTROL: a whole screen carries no such note", async () => {
  assert.doesNotMatch(await tail(live({ reconstructed: false })), /rebuilt/i);
  assert.doesNotMatch(await tail(live({})), /rebuilt/i, "an older service sends no field, which is not a warning");
});
