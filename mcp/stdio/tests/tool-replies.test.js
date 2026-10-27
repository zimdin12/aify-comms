// The JS tools still do what the tool-reply fixtures recorded (0.9.11 slice 2).
//
// The fixtures are what the service's Python copy is held to, recorded from these tools. While Codex runs
// them, a change here that is not re-recorded would leave the two copies telling agents different things.
// A deliberate change re-records (`node tests/tool-replies/record.mjs`) and the diff shows what moved.

import assert from "node:assert/strict";
import test from "node:test";

import { readFileSync } from "node:fs";

import { SCHEMAS_FILE, jsToolClient, loadCases, runCase, startService, toolSchemas } from "./tool-replies/replay.mjs";

const cases = loadCases();
const service = await startService();
const client = await jsToolClient(service.url);

test("there are fixture cases to replay", () => {
  assert.ok(cases.length > 0);
});

for (const testCase of cases) {
  test(`${testCase.file}: ${testCase.name}`, async () => {
    assert.ok(typeof testCase.text === "string", "not recorded yet: run node tests/tool-replies/record.mjs");
    const actual = await runCase(client, service, testCase);
    assert.deepEqual(actual, { requests: testCase.requests, text: testCase.text, isError: testCase.isError });
  });
}

test("the JS tools' public schemas are still the recorded ones", async () => {
  assert.deepEqual(await toolSchemas(client), JSON.parse(readFileSync(SCHEMAS_FILE, "utf8")));
});

test.after(async () => {
  await client.close();
  await service.close();
});
