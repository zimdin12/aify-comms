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
//
// SPLIT INTO A FUNCTION AND AN ENTRY POINT, 2026-08-17. It was a bare script: the top level did the
// work, so importing it RAN a live quota check, and `every-module-is-imported-by-a-test.test.js`
// recorded it as one of two modules that could not be import-tested. That gate's own note says the
// answer is "an exported entry point or an end-to-end harness — a change to the module rather than to
// this list", so this is that change. `runUsagePreflight` takes its check and its logger, and the
// script tail below runs it ONLY when this file is the process entry point.
//
// The rendered lines are byte-identical to what the script printed before; `install.sh` invokes it the
// same way and still sees the same stdout.

import { fileURLToPath } from "node:url";
import path from "node:path";

import { checkOpenAiUsageAccess } from "./usage-collector.js";

// A verdict that could not be obtained is still a verdict. The catch shape is part of the contract:
// callers (and the --json consumer) get the same four fields whether the check answered or threw.
export function preflightErrorVerdict(error) {
  return {
    ok: false,
    code: "error",
    message: "OpenAI usage preflight could not run.",
    detail: String(error && error.message ? error.message : error),
  };
}

// The operator-facing rendering. A verdict in, lines out — no I/O, so the wording is assertable.
//
// WHY THE FAILURE CASE IS SO WORDY: this runs during an install, and a WARNING with no context reads
// as "the install is broken". The last line exists to say it is not.
export function usagePreflightLines(verdict, { json = false } = {}) {
  const r = verdict || {};
  if (json) return [JSON.stringify(r)];
  if (r.ok) return [`  [usage] OK — ${r.message}`];
  const lines = ["", `  [usage] WARNING — ${r.message}`];
  if (r.detail) lines.push(`  [usage] ${r.detail}`);
  lines.push("  [usage] Everything else works; only the OpenAI quota panel is affected.");
  lines.push("");
  return lines;
}

export async function runUsagePreflight({
  argv = process.argv,
  check = checkOpenAiUsageAccess,
  log = console.log,
} = {}) {
  const json = argv.includes("--json");
  const verdict = await Promise.resolve()
    .then(check)
    .catch(preflightErrorVerdict);
  for (const line of usagePreflightLines(verdict, { json })) log(line);
  return verdict;
}

function isEntryPoint() {
  const invoked = process.argv[1];
  if (!invoked) return false;
  try {
    return path.resolve(invoked) === path.resolve(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isEntryPoint()) {
  await runUsagePreflight();
  // Always 0. Usage quota is advisory; a missing codex install must never fail the install.
  process.exit(0);
}
