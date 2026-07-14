#!/usr/bin/env node
// Install-time verdict on the OpenAI/ChatGPT usage pool.
//
// That pool fails SILENTLY by design: no token -> fall back to a codex rollout -> render a stale
// number. Nothing errors, so the dashboard's ChatGPT quota can be dead for weeks and look fine
// (it was). This prints a verdict an operator — or an installing AGENT — can act on, and it
// PROVES the connection rather than just finding a file: an expired token passes a file check
// and fails for real.
//
// Always exits 0. Usage quota is advisory; a missing codex install must never fail the install.
// `--json` prints a machine-readable line for an agent to parse.

import { checkOpenAiUsageAccess } from "./usage-collector.js";

const json = process.argv.includes("--json");

const r = await checkOpenAiUsageAccess().catch((err) => ({
  ok: false,
  code: "error",
  message: "OpenAI usage preflight could not run.",
  detail: String(err && err.message ? err.message : err),
}));

if (json) {
  console.log(JSON.stringify(r));
} else if (r.ok) {
  console.log(`  [usage] OK — ${r.message}`);
} else {
  console.log("");
  console.log(`  [usage] WARNING — ${r.message}`);
  if (r.detail) console.log(`  [usage] ${r.detail}`);
  console.log("  [usage] Everything else works; only the OpenAI quota panel is affected.");
  console.log("");
}

process.exit(0);
