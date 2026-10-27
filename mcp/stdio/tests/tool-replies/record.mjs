#!/usr/bin/env node
// Records what the JS tools do for every fixture case into its file: `node tests/tool-replies/record.mjs`.
// Run it after writing a case, or after a deliberate change to a JS tool, then read the diff: the recorded
// text is what the service's Python copy is held to.

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { CASES_DIR, jsToolClient, runCase, startService } from "./replay.mjs";

const service = await startService();
const client = await jsToolClient(service.url);
for (const file of readdirSync(CASES_DIR).filter((name) => name.endsWith(".json")).sort()) {
  const target = path.join(CASES_DIR, file);
  const cases = JSON.parse(readFileSync(target, "utf8"));
  const recorded = [];
  // Everything but the recorded fields is the case as written, and is kept as written.
  for (const { requests, text, isError, ...written } of cases) {
    recorded.push({ ...written, ...(await runCase(client, service, written)) });
  }
  writeFileSync(target, `${JSON.stringify(recorded, null, 2)}\n`);
  console.log(`${file}: ${recorded.length} case(s)`);
}
await client.close();
await service.close();
