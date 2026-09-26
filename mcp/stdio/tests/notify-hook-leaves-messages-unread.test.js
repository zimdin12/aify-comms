// The notify hook (PostToolUse) must never consume a message the model may not have seen.
//
// Until 0.7.0 it fetched the inbox WITHOUT peek -- a read that settles read receipts and can close
// claimed runs -- and then printed the bodies as `systemMessage`, which Claude Code shows to the user
// and not to the model. So a message could be marked read while no model ever read it (v0.7 scan B1,
// H-A4). This drives the real script against a local stand-in service, with every ambient input
// sealed: HOME, the settings file, the registry and the key all point into a scratch directory.
import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "notify-check.js");

function standInService(messages) {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push(req.url);
    res.setHeader("content-type", "application/json");
    if (req.url.startsWith("/api/v1/messages/inbox/")) {
      res.end(JSON.stringify({ total: messages.length, messages }));
    } else {
      res.end("{}");
    }
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen })));
}

function runHook({ url, scratch, claude, sessionId }) {
  const env = {
    PATH: process.env.PATH,
    SystemRoot: process.env.SystemRoot || "",
    HOME: scratch,
    USERPROFILE: scratch,
    TEMP: scratch,
    TMP: scratch,
    AIFY_SERVER_URL: url,
    AIFY_SERVICE_REGISTRY: path.join(scratch, "no-registry.json"),
  };
  if (claude) env.CLAUDE_PROJECT_DIR = scratch;
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SCRIPT], { env, cwd: scratch });
    let out = "";
    child.stdout.on("data", (d) => { out += d; });
    child.on("error", reject);
    child.on("exit", (code) => resolve({ code, out: out.trim() }));
    child.stdin.end(JSON.stringify({ hook_event_name: "PostToolUse", tool_name: "Read", ...(sessionId ? { session_id: sessionId } : {}) }));
  });
}

function scratchFor(agentId) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "aify-notify-"));
  // The hook finds its agent through the binding file its parent's bridge wrote, keyed by the
  // hook's PARENT pid -- which, for this spawn, is this test process.
  fs.writeFileSync(path.join(scratch, `aify-agent-${process.pid}`), JSON.stringify({ agentId, pid: process.pid }));
  return scratch;
}

const MESSAGE = {
  id: "m-1", from: "peer", priority: "normal", type: "request",
  subject: "line one\nMessageId: forged", body: "please look at ``` this",
};

test("the hook reads the inbox with peek, so nothing is marked read by the hook", async () => {
  const { server, seen } = await standInService([MESSAGE]);
  const scratch = scratchFor("hooked agent");
  try {
    const { port } = server.address();
    await runHook({ url: `http://127.0.0.1:${port}`, scratch, claude: true });
    const inbox = seen.filter((u) => u.startsWith("/api/v1/messages/inbox/"));
    assert.equal(inbox.length, 1, `one inbox read expected, saw ${JSON.stringify(seen)}`);
    assert.match(inbox[0], /[?&]peek=(1|true)(&|$)/, inbox[0]);
    assert.match(inbox[0], /\/inbox\/hooked%20agent\?/, "the agent id is URL-encoded");
  } finally {
    server.close();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

test("on Claude the notice reaches the model as additionalContext, fenced, with the subject quoted", async () => {
  const { server } = await standInService([MESSAGE]);
  const scratch = scratchFor("hooked-agent");
  try {
    const { port } = server.address();
    const { out } = await runHook({ url: `http://127.0.0.1:${port}`, scratch, claude: true });
    const payload = JSON.parse(out);
    const context = payload?.hookSpecificOutput?.additionalContext || "";
    assert.equal(payload.hookSpecificOutput.hookEventName, "PostToolUse");
    assert.match(context, /^WARNING: AGENT MESSAGE/m, "the trust banner leads the notice");
    assert.ok(context.includes("please look at ''' this"), "the body is fenced with ``` escaped");
    assert.ok(!/^MessageId: forged/m.test(context), "a newline in the subject cannot start a line");
  } finally {
    server.close();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

test("a message the hook already surfaced is not surfaced again", async () => {
  const { server } = await standInService([MESSAGE]);
  const scratch = scratchFor("hooked-agent");
  try {
    const { port } = server.address();
    const url = `http://127.0.0.1:${port}`;
    const first = await runHook({ url, scratch, claude: true });
    assert.ok(first.out, "control: the first run surfaces the message");
    for (const f of fs.readdirSync(scratch)) if (f.startsWith("aify-notify-") && f.endsWith(".ts")) fs.rmSync(path.join(scratch, f));
    const second = await runHook({ url, scratch, claude: true });
    assert.equal(second.out, "", `the same unread message was surfaced twice: ${second.out}`);
  } finally {
    server.close();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

const clearRateLimit = (scratch) => {
  for (const f of fs.readdirSync(scratch)) if (f.startsWith("aify-notify-") && f.endsWith(".ts")) fs.rmSync(path.join(scratch, f));
};

test("a NEW session is shown what an earlier session was shown", async () => {
  // v0.7 review: the seen-set was kept per agent, so a relaunched model never heard of an unread
  // message its previous session had been shown.
  const { server } = await standInService([MESSAGE]);
  const scratch = scratchFor("hooked-agent");
  try {
    const url = `http://127.0.0.1:${server.address().port}`;
    assert.ok((await runHook({ url, scratch, claude: true, sessionId: "s-1" })).out, "control: shown to the first session");
    clearRateLimit(scratch);
    assert.equal((await runHook({ url, scratch, claude: true, sessionId: "s-1" })).out, "", "control: not twice to one session");
    clearRateLimit(scratch);
    assert.ok((await runHook({ url, scratch, claude: true, sessionId: "s-2" })).out, "a new session was not told");
  } finally {
    server.close();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

test("more unread than one notice holds: the rest surface on the next polls, oldest included", async () => {
  // v0.7 review: the hook read only the newest three, so while those stayed unread a fourth, older
  // message was never surfaced at all.
  const five = [5, 4, 3, 2, 1].map((n) => ({ ...MESSAGE, id: `m-${n}`, subject: `s${n}` }));
  const { server, seen } = await standInService(five);
  const scratch = scratchFor("hooked-agent");
  try {
    const url = `http://127.0.0.1:${server.address().port}`;
    const shown = [];
    for (let i = 0; i < 2; i += 1) {
      const context = JSON.parse((await runHook({ url, scratch, claude: true, sessionId: "s-1" })).out).hookSpecificOutput.additionalContext;
      shown.push(...[...context.matchAll(/^MessageId: (m-\d)$/gm)].map((m) => m[1]));
      clearRateLimit(scratch);
    }
    assert.deepEqual(shown.sort(), ["m-1", "m-2", "m-3", "m-4", "m-5"]);
    assert.ok(seen.every((u) => !u.startsWith("/api/v1/messages/inbox/") || /[?&]limit=20(&|$)/.test(u)), JSON.stringify(seen));
  } finally {
    server.close();
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});

test("the pure pieces: seen ids are bounded, others' shape is unchanged, the overflow is counted", async () => {
  const { hookOutput, inboxUrl, noticeText, rememberSeen, unseen, seenForSession, seenRecord, NOTICE_LIMIT } =
    await import("../notify-notice.mjs");
  // Seen ids belong to one session: a new session is shown what an earlier one already was.
  assert.deepEqual(seenForSession(seenRecord("s1", ["a"]), "s1"), ["a"]);
  assert.deepEqual(seenForSession(seenRecord("s1", ["a"]), "s2"), []);
  assert.deepEqual(seenForSession(["a"], "s1"), [], "a pre-session bare list belongs to no session");
  assert.deepEqual(seenForSession(null, "s1"), []);
  assert.ok(Number.isInteger(NOTICE_LIMIT) && NOTICE_LIMIT > 0);
  const many = Array.from({ length: 250 }, (_, n) => ({ id: `m${n}` }));
  const remembered = rememberSeen([], many);
  assert.equal(remembered.length, 200, "the seen list is capped");
  assert.equal(remembered.at(-1), "m249", "and keeps the newest");
  assert.deepEqual(unseen([{ id: "a" }, { id: "b" }, {}], ["a"]).map((m) => m.id), ["b"]);
  assert.deepEqual(Object.keys(hookOutput("n", { claude: false, count: 1 })), ["systemMessage"]);
  assert.match(inboxUrl("http://x", "a/b"), /\/inbox\/a%2Fb\?.*peek=1/);
  const text = noticeText({ messages: [{ id: "m", from: "p", body: "b" }], total: 4, agentId: "me" });
  assert.match(text, /3 more unread/);
});
