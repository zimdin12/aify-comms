// comms_dashboard's answer lands in the agent's transcript, logs, and anything relayed from them. Until
// 0.7.0 it printed the dashboard URL with `?api_key=<the service key>` in it (v0.7 scan B8). The key
// may still be used to OPEN the page; it is never printed.
import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const TOOL = pathToFileURL(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dashboard-tool.mjs")).href;

test("remote mode names the dashboard without the key", () => {
  const script = `
    const { registerDashboardTool } = await import(${JSON.stringify(TOOL)});
    const { z } = await import("zod");
    let handler;
    registerDashboardTool({ tool: (_n, _d, _s, h) => { handler = h; } }, z);
    const res = await handler({ open: false });
    process.stdout.write(res.content[0].text);
  `;
  const run = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    cwd: path.dirname(fileURLToPath(import.meta.url)),
    env: {
      PATH: process.env.PATH, SystemRoot: process.env.SystemRoot || "",
      HOME: os.tmpdir(), USERPROFILE: os.tmpdir(),
      AIFY_SERVER_URL: "http://127.0.0.1:1", AIFY_API_KEY: "the-secret-key-1234",
      AIFY_SERVICE_REGISTRY: path.join(os.tmpdir(), "no-such-registry.json"),
    },
    encoding: "utf8",
  });
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stdout, /Dashboard: http:\/\/127\.0\.0\.1:1\//, "control: the URL is still named");
  assert.ok(!run.stdout.includes("the-secret-key-1234"), `the key was printed: ${run.stdout}`);
});
