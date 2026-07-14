// Registering must TURN STATUS ON for a session that started without --aify-agent.
//
// Before this, a bare `claude-aify` session dropped its session id on the floor (the hook
// had no agent id to key by) and the turn detector never armed (it read AIFY_AGENT_ID once,
// at boot). comms_register told the bridge who it was — and nothing used that. The agent
// registered, messaged and heartbeated perfectly while its status latched forever, with no
// visible error anywhere. That is the general-manager incident, and it is bad UX besides:
// an operator who registers reasonably expects status to work.
//
// The chain under test: hook captures session id keyed by the claude pid -> comms_register
// claims it into the agent-keyed store -> the detector can resolve the transcript.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { handleClaudeSessionHook } from "../claude-session-hook.js";
import {
  claudeSessionPidCapturePath,
  claudeSessionStorePath,
  readCapturedClaudeSessionIdForPid,
  readClaudeSessionId,
  writeClaudeSessionId,
} from "../claude-session-store.js";
import { writeAgentBindingFile } from "../binding-file.js";

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aify-late-identity-"));
}

const hookPayload = (sessionId) => JSON.stringify({
  session_id: sessionId,
  cwd: "/home/dev/projects",
  transcript_path: `/home/dev/.claude/projects/x/${sessionId}.jsonl`,
});

test("anonymous session: the hook CAPTURES the session id instead of dropping it", () => {
  const dir = tmpdir();
  handleClaudeSessionHook({ stdin: hookPayload("sess-anon"), env: {}, dir, ppid: 4242 });

  // The old behaviour was to return early and write nothing at all.
  assert.equal(readCapturedClaudeSessionIdForPid({ pid: 4242, dir }), "sess-anon");
  assert.ok(fs.existsSync(claudeSessionPidCapturePath(4242, dir)));
});

test("comms_register can then claim that capture into the agent-keyed store", () => {
  const dir = tmpdir();
  handleClaudeSessionHook({ stdin: hookPayload("sess-anon"), env: {}, dir, ppid: 4242 });

  // What server.js's claimCapturedClaudeSession does on register:
  assert.equal(readClaudeSessionId({ agentId: "comms-tech-lead", dir }), null);
  const captured = readCapturedClaudeSessionIdForPid({ pid: 4242, dir });
  writeClaudeSessionId({ sessionId: captured, agentId: "comms-tech-lead", dir });

  // The detector can now resolve THIS session's transcript -> status works.
  assert.equal(readClaudeSessionId({ agentId: "comms-tech-lead", dir }), "sess-anon");
});

test("once registered, later hook fires key the store via the BINDING file (no env needed)", () => {
  const dir = tmpdir();
  writeAgentBindingFile({ pid: 4242, agentId: "comms-tech-lead", bridgeId: "b1", dir });

  handleClaudeSessionHook({ stdin: hookPayload("sess-2"), env: {}, dir, ppid: 4242 });

  assert.equal(readClaudeSessionId({ agentId: "comms-tech-lead", dir }), "sess-2");
  // It went straight to the agent-keyed store — no pid capture needed.
  assert.equal(readCapturedClaudeSessionIdForPid({ pid: 4242, dir }), null);
});

test("env identity still wins and behaves exactly as before (no regression)", () => {
  const dir = tmpdir();
  handleClaudeSessionHook({
    stdin: hookPayload("sess-3"),
    env: { AIFY_AGENT_ID: "general-manager" },
    dir,
    ppid: 4242,
  });

  assert.equal(readClaudeSessionId({ agentId: "general-manager", dir }), "sess-3");
  assert.ok(fs.existsSync(claudeSessionStorePath("general-manager", dir)));
  assert.equal(readCapturedClaudeSessionIdForPid({ pid: 4242, dir }), null);
});

test("the pid capture must NOT be mistaken for an agent store by the wrapper's recovery glob", () => {
  const dir = tmpdir();
  handleClaudeSessionHook({ stdin: hookPayload("sess-anon"), env: {}, dir, ppid: 4242 });

  // claude-aify recovers a handle->agent by globbing `aify-claude-session-*.json` and taking
  // the filename suffix as the agent id. If the pid capture matched that glob, it would
  // "recover" an agent id of `pid-4242` and confidently register garbage.
  const captureName = path.basename(claudeSessionPidCapturePath(4242, dir));
  assert.ok(!/^aify-claude-session-.*\.json$/.test(captureName), captureName);

  const globbed = fs.readdirSync(dir).filter((f) => /^aify-claude-session-.*\.json$/.test(f));
  assert.deepEqual(globbed, []);
});

test("a malformed/empty hook payload never throws (hooks block claude if they fail)", () => {
  const dir = tmpdir();
  assert.doesNotThrow(() => handleClaudeSessionHook({ stdin: "", env: {}, dir, ppid: 1 }));
  assert.doesNotThrow(() => handleClaudeSessionHook({ stdin: "not json", env: {}, dir, ppid: 1 }));
  assert.doesNotThrow(() => handleClaudeSessionHook({ stdin: "{}", env: {}, dir, ppid: 1 }));
  assert.doesNotThrow(() => handleClaudeSessionHook({ stdin: hookPayload("s"), env: {}, dir, ppid: 0 }));
});
