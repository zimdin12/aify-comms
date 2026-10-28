// A Claude session on a channel route is not shown, by the notify hook, the body of a message whose queued channel
// run will bring it anyway; it is told the message exists instead (design 2, narrowed; notify-notice.mjs header).
//
// Drives the real notify-check.js against a stand-in inbox that, like the real route, adds `queuedForChannel` only
// when asked with `queuedChannel=1`. What decides a message is queued for the channel is the service's, and is
// tested against real runs in service/tests/test_the_inbox_names_messages_queued_for_the_channel.py.
import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "notify-check.js");
const CLAUDE_ON_PLUGIN = { AIFY_RUNTIME: "claude-code", AIFY_CHANNEL_ROUTE: "plugin" };

function standIn(population) {
  const urls = [];
  const server = http.createServer((req, res) => {
    urls.push(req.url);
    res.setHeader("content-type", "application/json");
    if (!req.url.startsWith("/api/v1/messages/inbox/")) return res.end("{}");
    const query = new URL(req.url, "http://stand-in").searchParams;
    const offset = Number(query.get("offset") || 0);
    const asked = query.has("queuedChannel");
    const page = population.slice(offset, offset + Number(query.get("limit") || 200)).map(({ queued, ...m }) =>
      asked ? { ...m, queuedForChannel: Boolean(queued) } : m);
    res.end(JSON.stringify({ total: population.length, messages: page }));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () =>
    resolve({ server, urls, url: `http://127.0.0.1:${server.address().port}` })));
}

async function withScratch(fn) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "aify-notify-queued-"));
  try { return await fn(scratch); } finally { fs.rmSync(scratch, { recursive: true, force: true }); }
}

function runHook({ url, scratch, env = {} }) {
  // A poll right after another is rate-limited; each run here is a fresh poll.
  for (const f of fs.readdirSync(scratch)) if (f.startsWith("aify-notify-") && f.endsWith(".ts")) fs.rmSync(path.join(scratch, f));
  const childEnv = {
    PATH: process.env.PATH, SystemRoot: process.env.SystemRoot || "",
    HOME: scratch, USERPROFILE: scratch, TEMP: scratch, TMP: scratch,
    AIFY_COMMS_URL: url, AIFY_AGENT_ID: "lead", CLAUDE_PROJECT_DIR: scratch,
    AIFY_SERVICE_REGISTRY: path.join(scratch, "no-registry.json"),
    ...env,
  };
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SCRIPT], { env: childEnv, cwd: scratch });
    let out = "";
    child.stdout.on("data", (d) => { out += d; });
    child.on("error", reject);
    child.on("exit", () => {
      const text = out.trim();
      resolve(text ? JSON.parse(text).hookSpecificOutput.additionalContext : "");
    });
    child.stdin.end(JSON.stringify({ hook_event_name: "PostToolUse", tool_name: "Read", session_id: "s-1" }));
  });
}

const message = (n, queued) => ({ id: `m-${n}`, from: "peer", priority: "normal", subject: `s${n}`, body: `body of ${n}`, queued });
const shownIds = (context) => [...context.matchAll(/^MessageId: (m-\d+)$/gm)].map((m) => m[1]);

test("every candidate queued for the channel and nothing claims it: a cue names them, without a body", async () => {
  const population = [message(2, true), message(1, true)];
  const { server, urls, url } = await standIn(population);
  try {
    await withScratch(async (scratch) => {
      const first = await runHook({ url, scratch, env: CLAUDE_ON_PLUGIN });
      assert.ok(urls.some((u) => /[?&]queuedChannel=1(&|$)/.test(u)), `the session did not ask: ${JSON.stringify(urls)}`);
      assert.doesNotMatch(first, /body of/, "a queued body was repeated");
      assert.match(first, /2 unread message\(s\) are queued for this session's channel/);
      assert.match(first, /from peer, MessageId: m-2\n- from peer, MessageId: m-1/, "the cue names each id and sender");
      assert.match(first, /\(2 unread in this agent's whole inbox\)/, "the total is labelled as the whole inbox");
      assert.match(first, /comms_inbox\(agentId="lead", messageId="<id>"\)/, "the cue says how to recover one");

      assert.equal(await runHook({ url, scratch, env: CLAUDE_ON_PLUGIN }), "", "the same cue was printed twice");

      // The run failed: the message is no longer queued for the channel, and its body was never marked shown.
      population[1].queued = false;
      const third = await runHook({ url, scratch, env: CLAUDE_ON_PLUGIN });
      assert.deepEqual(shownIds(third), ["m-1"], "a body left to a channel that failed was never shown");
      assert.match(third, /body of 1/);
    });
  } finally {
    server.close();
  }
});

test("a session off the channel route, and codex on one, keep every body", async () => {
  const { server, urls, url } = await standIn([message(1, true)]);
  try {
    for (const env of [{ AIFY_RUNTIME: "claude-code" }, { AIFY_RUNTIME: "codex", AIFY_CHANNEL_ROUTE: "plugin" }]) {
      urls.length = 0;
      await withScratch(async (scratch) => {
        assert.match(await runHook({ url, scratch, env }), /body of 1/, `${JSON.stringify(env)} lost the body`);
        assert.ok(!urls.some((u) => u.includes("queuedChannel")), `${JSON.stringify(env)} asked for the projection`);
      });
    }
    // CONTROL: the dev route opts in exactly as the plugin route does.
    await withScratch(async (scratch) => {
      assert.doesNotMatch(await runHook({ url, scratch, env: { AIFY_RUNTIME: "claude-code", AIFY_CHANNEL_ROUTE: "dev" } }), /body of 1/);
    });
  } finally {
    server.close();
  }
});

test("the pure pieces: who opts in, whose cues a session keeps, what a cue says", async () => {
  const { channelSessionOptsIn, cuedForSession, queuedCueText, seenRecord } = await import("../notify-notice.mjs");
  assert.equal(channelSessionOptsIn({ AIFY_RUNTIME: "claude-code", AIFY_CHANNEL_ROUTE: "dev" }), true);
  assert.equal(channelSessionOptsIn({ AIFY_RUNTIME: "claude-code", AIFY_CHANNEL_ROUTE: "x" }), false, "an unknown route");
  assert.equal(channelSessionOptsIn({ AIFY_CHANNEL_ROUTE: "plugin" }), false, "no runtime named");

  assert.deepEqual(cuedForSession(seenRecord("s1", ["a"], ["q"]), "s1"), ["q"]);
  assert.deepEqual(cuedForSession(seenRecord("s1", ["a"], ["q"]), "s2"), [], "another session's cues");
  assert.deepEqual(cuedForSession({ session: "s1", ids: ["a"] }, "s1"), [], "a record written before cues existed");
  assert.deepEqual(cuedForSession(null, "s1"), []);

  const cue = queuedCueText({ queued: [{ id: "m-1", from: "p", body: "secret body" }], total: 7, agentId: "me" });
  assert.doesNotMatch(cue, /secret body/);
  assert.match(cue, /1 unread message\(s\).*\n- from p, MessageId: m-1\n/s);
});

test("skipped messages take no place in the limit, and a page of them does not hide the one behind it", async () => {
  // Newest first: three queued ahead of three ordinary ones. NOTICE_LIMIT is 3, so all three ordinary bodies
  // fit in one notice only if the queued ones are not counted against it.
  const mixed = [message(6, true), message(5, true), message(4, true), message(3), message(2), message(1)];
  const one = await standIn(mixed);
  try {
    await withScratch(async (scratch) => {
      const context = await runHook({ url: one.url, scratch, env: CLAUDE_ON_PLUGIN });
      assert.deepEqual(shownIds(context), ["m-3", "m-2", "m-1"]);
      assert.match(context, /3 unread message\(s\) are queued/, "the bodies and the cue arrive in one notice");
    });
  } finally {
    one.server.close();
  }

  // Twenty queued fill the first page; the ordinary message is on the second.
  const behind = [...Array.from({ length: 20 }, (_, n) => message(40 - n, true)), message(1)];
  const two = await standIn(behind);
  try {
    await withScratch(async (scratch) => {
      assert.deepEqual(shownIds(await runHook({ url: two.url, scratch, env: CLAUDE_ON_PLUGIN })), ["m-1"]);
      assert.ok(two.urls.some((u) => /[?&]offset=20(&|$)/.test(u)), "control: the hook read the second page");
    });
  } finally {
    two.server.close();
  }
});
