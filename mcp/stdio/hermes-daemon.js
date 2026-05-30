#!/usr/bin/env node
// ensureDaemon — guarantee exactly ONE long-lived `hermes gateway run` daemon
// is up with the api_server platform enabled, idempotently.
//
// Managed hermes delivery POSTs to the api_server platform (HTTP/SSE on
// http://127.0.0.1:8642) that runs in-process inside one shared
// `hermes gateway run` daemon. This helper is the ensure-up step: probe first,
// and only spawn (DETACHED) if the daemon isn't already answering — so calling
// it from every per-agent sidecar launch is safe.
//
// spawn + probe are injected so tests never launch a real process or touch a
// real socket. Contract:
// docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import { spawn as nodeSpawn } from "node:child_process";
import { probeApiServer } from "./hermes-version.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Ensure one api_server-enabled hermes gateway daemon is up.
//   - First probe; if available → { started:false, version } (NO spawn).
//   - Else spawn `hermes gateway run --replace` detached with API_SERVER_* env,
//     unref it, and poll probe until healthy or healthTimeoutMs elapses.
//   - On success → { started:true, version, pid }. On timeout → throw.
export async function ensureDaemon({
  baseUrl = "http://127.0.0.1:8642",
  key,
  port = 8642,
  host = "127.0.0.1",
  hermesCmd = "hermes",
  spawn = nodeSpawn,
  probe = probeApiServer,
  healthTimeoutMs = 15000,
  pollMs = 300,
} = {}) {
  // 1. Idempotent fast-path: already up → no spawn.
  const initial = await probe({ baseUrl, key });
  if (initial && initial.available) {
    return { started: false, version: initial.version };
  }

  // 2. Spawn the daemon DETACHED so it outlives this bridge process.
  const child = spawn(hermesCmd, ["gateway", "run", "--replace"], {
    env: {
      ...process.env,
      API_SERVER_ENABLED: "1",
      API_SERVER_KEY: key,
      API_SERVER_PORT: String(port),
      API_SERVER_HOST: host,
    },
    detached: true,
    stdio: "ignore",
  });
  if (child && typeof child.unref === "function") child.unref();

  // 3. Poll for health until the daemon answers or we time out.
  const deadline = Date.now() + healthTimeoutMs;
  for (;;) {
    const res = await probe({ baseUrl, key });
    if (res && res.available) {
      return { started: true, version: res.version, pid: child ? child.pid : undefined };
    }
    if (Date.now() >= deadline) break;
    await sleep(pollMs);
  }

  throw new Error(
    `[hermes] hermes gateway daemon did not become healthy within ${healthTimeoutMs}ms — ` +
      "check `hermes gateway run` / API_SERVER_* env (API_SERVER_ENABLED, " +
      `API_SERVER_KEY, API_SERVER_PORT=${port}, API_SERVER_HOST=${host}).`,
  );
}
