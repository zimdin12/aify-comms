// `claude-aify --resume <id>` without `--aify-agent` recovers the agent by asking the service who owns
// the session. The launcher curled `/api/v1/agents` with no key, so once the service required one the
// lookup got a 401 and the session came up anonymous (v0.7 docs review, D36). It now asks the bridge's
// `agent-for-handle.mjs`, which resolves the key the way every bridge call does.
//
// The REAL rendered launcher runs here, against a service that refuses a request without the key.
// It is rendered with AIFY_HOME at this checkout, so the launcher calls this checkout's bridge rather
// than whatever is installed on the machine running the suite. The service is a separate process
// because the harness runs the launcher synchronously.

import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { sealedChildEnv } from "./_child-env.mjs";
import { runWrapper } from "./wrapper-harness.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO = path.resolve(STDIO, "..", "..");
const KEY = "resume-recovery-key";
const SEEDED_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";

const SERVICE = `
const http = require("http");
const server = http.createServer((req, res) => {
  if (req.headers["x-api-key"] !== ${JSON.stringify(KEY)}) { res.writeHead(401).end("{}"); return; }
  res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
    agents: { "cc-recovered": { runtime: "claude-code", sessionHandle: ${JSON.stringify(SEEDED_ID)} } },
  }));
});
server.listen(0, "127.0.0.1", () => console.log(server.address().port));
`;

function startService() {
  const child = spawn(process.execPath, ["-e", SERVICE], { stdio: ["ignore", "pipe", "inherit"] });
  return new Promise((resolve) => {
    child.stdout.once("data", (chunk) => resolve({ child, url: `http://127.0.0.1:${String(chunk).trim()}` }));
  });
}

function renderAgainstThisCheckout() {
  const dir = fs.mkdtempSync(path.join(fs.realpathSync(process.env.TEMP || "/tmp"), "aify-resume-key-"));
  execFileSync("bash", [path.join(REPO, "install.sh"), "--client", "claude", "http://127.0.0.1:8899",
    "--emit-wrappers", dir], { stdio: "ignore", env: sealedChildEnv({ AIFY_HOME: REPO.replace(/\\/g, "/") }) });
  return dir;
}

function seedTranscript(home) {
  const dir = path.join(home, ".claude", "projects", "p");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${SEEDED_ID}.jsonl`), "{}\n");
}

test("--resume recovers the agent from a service that requires a key", async () => {
  const { child, url } = await startService();
  const rendered = renderAgainstThisCheckout();
  try {
    const launcher = path.join(rendered, "claude-aify");
    const run = (env) => runWrapper(launcher, {
      runtimeName: "claude", args: ["--resume", SEEDED_ID], prepareHome: seedTranscript,
      env: { AIFY_COMMS_URL: url, ...env },
    });
    const withKey = run({ AIFY_API_KEY: KEY });
    assert.equal(withKey.launched, true, withKey.stderr);
    assert.equal(withKey.env.AIFY_AGENT_ID, "cc-recovered", withKey.stderr);
    // Control: without a key the service refuses, recovery finds nothing, and the launch still happens.
    const withoutKey = run({});
    assert.equal(withoutKey.launched, true, withoutKey.stderr);
    assert.equal(withoutKey.env.AIFY_AGENT_ID, undefined);
    // The lookup asks the endpoint this launch is bound to. It used AIFY_COMMS_URL-or-localhost, which
    // ignored a baked remote endpoint (v0.7 review); here AIFY_COMMS_URL names a port nothing serves.
    const bound = run({ HARNESS_ENDPOINT: url, AIFY_COMMS_URL: "http://127.0.0.1:9", AIFY_API_KEY: KEY });
    assert.equal(bound.launched, true, bound.stderr);
    assert.equal(bound.env.AIFY_AGENT_ID, "cc-recovered", bound.stderr);
  } finally {
    child.kill();
    fs.rmSync(rendered, { recursive: true, force: true });
  }
});
