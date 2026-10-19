import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { discoverCodexLiveThreadId } from "../runtimes.js";
import { sealedChildEnv } from "./_child-env.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const fakeServer = resolve(__dirname, "fixtures/fake-codex-app-server.mjs");

function pickPort() {
  return new Promise((resolvePort, reject) => {
    const s = createServer();
    s.listen(0, "127.0.0.2", () => {
      const address = s.address();
      const port = address && typeof address === "object" ? address.port : 0;
      s.close(() => resolvePort(port));
    });
    s.on("error", reject);
  });
}

// The fake reports its own process cwd as every thread's cwd, so `serverCwd` is the workspace its
// threads belong to.
async function withFakeCodexAppServer(fn, serverCwd = process.cwd()) {
  const port = await pickPort();
  const url = `ws://127.0.0.2:${port}`;
  const child = spawn(process.execPath, [fakeServer, "--listen", url], {
    cwd: serverCwd,
    env: {
      ...sealedChildEnv(),
      FAKE_CODEX_RESIDENT_THREAD: "resident-thread-data-shape",
      FAKE_CODEX_THREAD_LIST_KEY: "data",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    await once(child.stdout, "data");
    return await fn(url);
  } finally {
    child.kill("SIGTERM");
    await once(child, "exit").catch(() => {});
  }
}

await withFakeCodexAppServer(async (url) => {
  const id = await discoverCodexLiveThreadId({ appServerUrl: url }, process.cwd());
  assert.equal(id, "resident-thread-data-shape");
});

// A fresh agent whose app server lists only threads from ANOTHER workspace discovers no thread. The
// fallback to the newest thread anywhere bound a fresh agent to the operator's own Codex Desktop
// conversation on 2026-10-19; `thread/list` lists every rollout on the machine, not the live ones.
{
  const elsewhere = mkdtempSync(join(tmpdir(), "aify-foreign-workspace-"));
  try {
    await withFakeCodexAppServer(async (url) => {
      assert.equal(await discoverCodexLiveThreadId({ appServerUrl: url }, process.cwd()), "",
        "a thread from another workspace must never be bound");
      // Control: the same server's thread IS found from the workspace it belongs to.
      assert.equal(await discoverCodexLiveThreadId({ appServerUrl: url }, realpathSync(elsewhere)),
        "resident-thread-data-shape");
    }, elsewhere);
  } finally {
    rmSync(elsewhere, { recursive: true, force: true });
  }
}

console.log("codex-live-thread-discovery.test.js: all assertions passed");
