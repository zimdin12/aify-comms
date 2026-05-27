import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { discoverCodexLiveThreadId } from "../runtimes.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const fakeServer = resolve(__dirname, "fixtures/fake-codex-app-server.mjs");

function pickPort() {
  return new Promise((resolvePort, reject) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const address = s.address();
      const port = address && typeof address === "object" ? address.port : 0;
      s.close(() => resolvePort(port));
    });
    s.on("error", reject);
  });
}

async function withFakeCodexAppServer(fn) {
  const port = await pickPort();
  const url = `ws://127.0.0.1:${port}`;
  const child = spawn(process.execPath, [fakeServer, "--listen", url], {
    cwd: process.cwd(),
    env: {
      ...process.env,
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

console.log("codex-live-thread-discovery.test.js: all assertions passed");
