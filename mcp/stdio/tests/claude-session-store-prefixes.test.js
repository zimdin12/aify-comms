#!/usr/bin/env node
// Two session stores in one directory, and the filename prefix that keeps them apart.
//
// `claude-session-store.js` holds claude's own session id twice over: keyed by AGENT ID (written once
// identity is known) and keyed by CLAUDE PID (written before it is, so a session launched without
// `--aify-agent` is not lost and can be promoted when `comms_register` arrives).
//
// THE INVARIANT IS CROSS-FILE AND NOTHING CHECKED IT. `install.sh` recovers an agent id by globbing
// `aify-claude-session-*.json` and STRIPPING that prefix — whatever is left becomes the agent id. If
// the pid-keyed file shared the prefix, the wrapper would "recover" an agent called `pid-1234` and
// bind a session to an agent that does not exist. The module's own comment says exactly this; the
// glob it is talking about lives in a shell script the JS suite never reads, so the two could drift
// apart with nothing failing.
//
// The glob is EXTRACTED FROM install.sh here rather than remembered, and required to appear exactly
// once, so a change to it on either side lands as a failure rather than as a silent widening.
//
// `writeCapturedClaudeSessionIdForPid` additionally had no test naming it at all.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  claudeSessionPidCapturePath,
  claudeSessionStorePath,
  readCapturedClaudeSessionIdForPid,
  readClaudeSessionId,
  writeCapturedClaudeSessionIdForPid,
  writeClaudeSessionId,
} from "../claude-session-store.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-session-store-"));

try {
  // ── the recovery glob, read off install.sh ─────────────────────────────────────────────────
  {
    const installer = fs.readFileSync(path.join(REPO, "install.sh"), "utf-8");
    const matches = [...installer.matchAll(/\/(aify-claude-[a-z-]*\*\.json)/g)].map((m) => m[1]);
    assert.equal(
      matches.length,
      1,
      `expected exactly one aify-claude-*.json glob in install.sh, found ${JSON.stringify(matches)} — `
        + "a second one is another recovery path that must be judged against these prefixes too",
    );
    const glob = matches[0];
    assert.equal(glob, "aify-claude-session-*.json", "the recovery glob changed shape");

    const toRegExp = (g) => new RegExp(`^${g.split("*").map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`);
    const recovers = toRegExp(glob);

    // The agent-keyed file IS what the wrapper is looking for.
    assert.ok(
      recovers.test(path.basename(claudeSessionStorePath("sc-coder", dir))),
      "the agent-keyed store must match the recovery glob — that is how the wrapper finds it",
    );

    // THE PID-KEYED FILE MUST NOT BE. Stripping the prefix off it would yield an agent id of
    // `pid-1234`, and the wrapper would bind a session to an agent nobody registered.
    for (const pid of [1234, 1, 999999]) {
      const name = path.basename(claudeSessionPidCapturePath(pid, dir));
      assert.ok(
        !recovers.test(name),
        `${name} matches the recovery glob — the wrapper would recover a bogus agent id from it`,
      );
    }

    // And the prefixes are genuinely distinct rather than one being a prefix of the other.
    const agentName = path.basename(claudeSessionStorePath("x", dir));
    const pidName = path.basename(claudeSessionPidCapturePath(1, dir));
    assert.ok(!agentName.startsWith(pidName.split("1.json")[0]));
    assert.ok(!pidName.startsWith(agentName.split("x.json")[0]));
  }

  // ── the agent-keyed store ──────────────────────────────────────────────────────────────────
  {
    const file = writeClaudeSessionId({ sessionId: "sess-abc", agentId: "sc-coder", dir });
    assert.ok(file, "a write with an agent id returns the path it wrote");
    assert.equal(readClaudeSessionId({ agentId: "sc-coder", dir }), "sess-abc");

    // No agent id is a NO-OP, not a file with an empty key — the whole point of agent keying is that
    // a machine-global guess caused cross-contamination.
    assert.equal(writeClaudeSessionId({ sessionId: "sess-abc", dir }), false);
    assert.equal(writeClaudeSessionId({ sessionId: "sess-abc", agentId: "   ", dir }), false);
    assert.equal(readClaudeSessionId({ dir }), null);

    // An id is trimmed on both write and read, so a padded value cannot make its own second file.
    writeClaudeSessionId({ sessionId: "sess-pad", agentId: "  padded  ", dir });
    assert.equal(readClaudeSessionId({ agentId: "padded", dir }), "sess-pad");

    // An EMPTY session id is stored but reads back as null: the file records that we looked, while
    // the reader refuses to hand a caller a blank handle to resume.
    writeClaudeSessionId({ sessionId: "", agentId: "blank", dir });
    assert.equal(readClaudeSessionId({ agentId: "blank", dir }), null);

    assert.equal(readClaudeSessionId({ agentId: "never-written", dir }), null, "a missing file is null");
  }

  // ── sanitisation, and the deliberate divergence from the hermes helper ─────────────────────
  {
    // Dots are KEPT and everything else invalid becomes an underscore. `hermes-endpoint.js` has a
    // function of the same name that folds runs into a dash instead — recorded as a ruled fork,
    // because unifying them would repoint files already on disk. This asserts THIS store's rule.
    assert.ok(claudeSessionStorePath("agent.1", dir).endsWith("aify-claude-session-agent.1.json"));
    assert.ok(claudeSessionStorePath("a/b\\c", dir).endsWith("aify-claude-session-a_b_c.json"));
    assert.ok(claudeSessionStorePath("a b", dir).endsWith("aify-claude-session-a_b.json"));
    assert.ok(claudeSessionStorePath("keep-_.", dir).endsWith("aify-claude-session-keep-_..json"));

    // A path separator in an agent id must not escape the store directory.
    const escaped = claudeSessionStorePath("../../etc/passwd", dir);
    assert.equal(path.dirname(escaped), dir, "a traversal attempt stays inside the store directory");
  }

  // ── the pid-keyed capture ──────────────────────────────────────────────────────────────────
  {
    assert.ok(writeCapturedClaudeSessionIdForPid({ sessionId: "sess-pid", pid: 4242, dir }));
    assert.equal(readCapturedClaudeSessionIdForPid({ pid: 4242, dir }), "sess-pid");
    assert.equal(readCapturedClaudeSessionIdForPid({ pid: 4243, dir }), null);

    // BOTH halves are required. Without a session id there is nothing to capture; without a pid
    // there is no key, and writing under pid 0 would collide across every such session.
    assert.equal(writeCapturedClaudeSessionIdForPid({ pid: 4242, dir }), false, "no session id");
    assert.equal(writeCapturedClaudeSessionIdForPid({ sessionId: "s", dir }), false, "no pid");
    assert.equal(writeCapturedClaudeSessionIdForPid({ sessionId: "s", pid: 0, dir }), false);
    assert.equal(writeCapturedClaudeSessionIdForPid({ sessionId: "   ", pid: 1, dir }), false);
    assert.equal(writeCapturedClaudeSessionIdForPid(), false, "no arguments at all");
    assert.equal(readCapturedClaudeSessionIdForPid({ pid: 0, dir }), null);
    assert.equal(readCapturedClaudeSessionIdForPid(), null);

    // A numeric string is a pid — the value arrives from an environment variable.
    assert.ok(writeCapturedClaudeSessionIdForPid({ sessionId: "sess-str", pid: "777", dir }));
    assert.equal(readCapturedClaudeSessionIdForPid({ pid: 777, dir }), "sess-str");
  }

  // ── best-effort: a broken store never throws at the caller ────────────────────────────────
  {
    // Session capture must never break the thing that called it, so corrupt and unreadable files
    // read as "no session" rather than as an exception on a hot path.
    fs.writeFileSync(claudeSessionStorePath("corrupt", dir), "{not json");
    assert.equal(readClaudeSessionId({ agentId: "corrupt", dir }), null);

    fs.writeFileSync(claudeSessionPidCapturePath(31337, dir), "{not json");
    assert.equal(readCapturedClaudeSessionIdForPid({ pid: 31337, dir }), null);

    fs.writeFileSync(claudeSessionStorePath("empty", dir), "   ");
    assert.equal(readClaudeSessionId({ agentId: "empty", dir }), null);

    fs.writeFileSync(claudeSessionStorePath("wrong-shape", dir), JSON.stringify({ notSessionId: "x" }));
    assert.equal(readClaudeSessionId({ agentId: "wrong-shape", dir }), null);

    fs.writeFileSync(claudeSessionPidCapturePath(31338, dir), JSON.stringify([1, 2, 3]));
    assert.equal(readCapturedClaudeSessionIdForPid({ pid: 31338, dir }), null);
  }
} finally {
  fs.rmSync(dir, { recursive: true, force: true });
}

console.log("claude-session-store-prefixes.test.js: all assertions passed");
