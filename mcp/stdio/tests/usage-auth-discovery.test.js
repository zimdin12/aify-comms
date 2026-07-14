// Finding the OpenAI token must not depend on the operating system — or on which tool the
// operator happens to have installed.
//
// Every bug in this area was a WRONG GUESS about a path, and every one failed SILENTLY (no
// token -> fall back to a stale rollout -> the dashboard's quota just never updates):
//   1. the Windows path used unconditionally, so a Linux/WSL host read a file that didn't exist;
//   2. the right OS but the wrong TOOL — hermes' auth.json on a default install is a POINTER
//      (`{"active_provider":"openai-codex"}`, no tokens) because it delegates to the CODEX store.
//
// So: search, don't guess. And say so out loud at install time.

import assert from "node:assert/strict";
import test from "node:test";

import { checkOpenAiUsageAccess, openAiAuthCandidates, toolHomeCandidates } from "../usage-collector.js";

const openAiJwt = () => {
  const payload = Buffer.from(JSON.stringify({ iss: "https://auth.openai.com" })).toString("base64url");
  return `ey.${payload}.sig`;
};

test("candidate stores cover BOTH tools on ANY platform — no process.platform branch", () => {
  const paths = openAiAuthCandidates().map((p) => p.replace(/\\/g, "/"));
  const has = (frag) => paths.some((p) => p.includes(frag));

  // codex is listed FIRST: hermes delegates to it, so on a default pair of installs the codex
  // store is the one that actually holds the JWT.
  assert.ok(paths[0].includes("codex"), `codex store must be searched first: ${paths[0]}`);

  assert.ok(has(".codex/auth.json"), "POSIX codex default");
  assert.ok(has(".hermes/auth.json"), "POSIX hermes default");
  assert.ok(has("AppData/Local/codex/auth.json"), "Windows codex default");
  assert.ok(has("AppData/Local/hermes/auth.json"), "Windows hermes default");
  assert.ok(has("Library/Application Support/codex/auth.json"), "macOS codex default");
  assert.ok(has(".config/codex/auth.json"), "XDG codex default");
});

test("a non-default install is honoured via the tool's OWN env var", () => {
  const prev = process.env.CODEX_HOME;
  process.env.CODEX_HOME = "/opt/custom-codex";
  try {
    assert.equal(toolHomeCandidates("codex")[0], "/opt/custom-codex", "CODEX_HOME must win");
  } finally {
    if (prev === undefined) delete process.env.CODEX_HOME;
    else process.env.CODEX_HOME = prev;
  }
});

test("preflight: codex missing / not logged in -> actionable warning, not silence", async () => {
  const hermesPointer = JSON.stringify({ version: 1, active_provider: "openai-codex" });
  const r = await checkOpenAiUsageAccess({ readHermesAuth: () => hermesPointer });
  assert.equal(r.ok, false);
  assert.equal(r.code, "no-token");
  assert.match(r.detail, /codex login/, "must tell the operator exactly what to do");
});

test("preflight PROVES the connection — a present-but-EXPIRED token is not 'healthy'", async () => {
  const r = await checkOpenAiUsageAccess({
    readHermesAuth: () => JSON.stringify({ tokens: { access_token: openAiJwt() } }),
    fetchImpl: async () => ({ ok: false, status: 401 }),
  });
  assert.equal(r.ok, false);
  assert.equal(r.code, "rejected", "a file check would have called this fine");
  assert.match(r.detail, /codex login/);
});

test("preflight: offline is reported as UNREACHABLE, not as a bad token", async () => {
  const r = await checkOpenAiUsageAccess({
    readHermesAuth: () => JSON.stringify({ tokens: { access_token: openAiJwt() } }),
    fetchImpl: async () => { throw new Error("getaddrinfo ENOTFOUND"); },
  });
  assert.equal(r.code, "unreachable", "must not blame the token for a network failure");
});

test("preflight: a working token reports OK", async () => {
  const r = await checkOpenAiUsageAccess({
    readHermesAuth: () => JSON.stringify({ tokens: { access_token: openAiJwt() } }),
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({}) }),
  });
  assert.equal(r.ok, true);
  assert.equal(r.code, "ok");
});
