// A Claude session on a channel route is not shown, by the notify hook, the body of a message whose queued channel
// run will bring it anyway; it is told the message exists instead (design 2; notify-notice.mjs header).
//
// Drives the real notify-check.js against a stand-in inbox that does what the real route does under
// `queuedChannel=2`: it leaves queued messages out of the population it pages and names the newest of them, by the
// run that holds each, in `queuedForChannel`. Which messages are queued is the service's rule, tested against real
// runs in service/tests/test_the_inbox_names_messages_queued_for_the_channel.py.
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

// `population` is newest first. A message with `run` set ({ at, id }) is queued for the channel on that run.
// `legacy` answers as the 2df43e00 service did: any queuedChannel value marks each message, nothing is left out.
function standIn(population, { legacy = false, delayMs = 0, onRequest = () => {} } = {}) {
  const urls = [];
  const server = http.createServer(async (req, res) => {
    urls.push(req.url);
    onRequest(req.url);
    if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
    res.setHeader("content-type", "application/json");
    if (!req.url.startsWith("/api/v1/messages/inbox/")) return res.end("{}");
    const query = new URL(req.url, "http://stand-in").searchParams;
    const asked = query.get("queuedChannel");
    const excluding = asked === "2" && !legacy;
    const queued = population.filter((m) => m.run);
    const paged = excluding ? population.filter((m) => !m.run) : population;
    const offset = Number(query.get("offset") || 0);
    const view = ({ run, ...m }) => (legacy && asked ? { ...m, queuedForChannel: Boolean(run) } : m);
    const answer = {
      total: paged.length,
      unreadTotal: population.length,
      messages: paged.slice(offset, offset + Number(query.get("limit") || 200)).map(view),
    };
    if (excluding) {
      const order = (a, b) => (a.run.at === b.run.at ? (a.run.id < b.run.id ? 1 : -1) : a.run.at < b.run.at ? 1 : -1);
      answer.queuedForChannel = {
        total: queued.length,
        newest: [...queued].sort(order).slice(0, 20)
          .map((m) => ({ messageId: m.id, from: m.from, requestedAt: m.run.at, runId: m.run.id })),
      };
    }
    res.end(JSON.stringify(answer));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () =>
    resolve({ server, urls, url: `http://127.0.0.1:${server.address().port}` })));
}

async function withScratch(fn) {
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "aify-notify-queued-"));
  try { return await fn(scratch); } finally { fs.rmSync(scratch, { recursive: true, force: true }); }
}

function runHook({ url, scratch, env = {}, session = "s-1" }) {
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
    child.stdin.end(JSON.stringify({ hook_event_name: "PostToolUse", tool_name: "Read", session_id: session }));
  });
}

const stamp = (n) => `2026-10-28T${String(Math.floor(n / 60) % 24).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}:00Z`;
const runAt = (n, id = `run-${String(n).padStart(4, "0")}`) => ({ at: stamp(n), id });
const message = (n, run = null) => ({ id: `m-${n}`, from: "peer", priority: "normal", subject: `s${n}`, body: `body of ${n}`, run });
const shownIds = (context) => [...context.matchAll(/^MessageId: (m-\d+)$/gm)].map((m) => m[1]);
const namedIds = (context) => [...context.matchAll(/^- from peer, MessageId: (m-\d+)$/gm)].map((m) => m[1]);
const poll = (url, scratch, session) => runHook({ url, scratch, env: CLAUDE_ON_PLUGIN, session });

test("every message queued and nothing claims them: a cue names them, without a body, once", async () => {
  const population = [message(2, runAt(2)), message(1, runAt(1))];
  const { server, urls, url } = await standIn(population);
  try {
    await withScratch(async (scratch) => {
      const first = await poll(url, scratch);
      assert.ok(urls.some((u) => /[?&]queuedChannel=2(&|$)/.test(u)), `the session did not ask: ${JSON.stringify(urls)}`);
      assert.doesNotMatch(first, /body of/, "a queued body was repeated");
      assert.match(first, /2 unread message\(s\) are queued for this session's channel/);
      assert.match(first, /\(2 unread in this agent's whole inbox\)/);
      assert.match(first, /Newly queued, 2 named here/, "the cue says how many it names");
      assert.deepEqual(namedIds(first), ["m-2", "m-1"]);
      assert.match(first, /comms_inbox\(agentId="lead"\) lists every unread message/, "the cue says how to see them all");

      assert.equal(await poll(url, scratch), "", "the same runs were named twice");
      assert.ok(namedIds(await poll(url, scratch, "s-2")).length === 2, "a new session is told again");

      // The run failed: the message is back in the paged population, and its body was never marked shown.
      population[1].run = null;
      assert.deepEqual(shownIds(await poll(url, scratch)), ["m-1"], "a body left to a channel that failed was never shown");
    });
  } finally {
    server.close();
  }
});

test("a session off the channel route, and codex on one, keep every body", async () => {
  const { server, urls, url } = await standIn([message(1, runAt(1))]);
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

test("a service older than version 2 keeps every body, names nothing and moves no mark", async () => {
  // Installed before the container is rebuilt: the 2df43e00 route reads queuedChannel=2 as its version 1.
  const population = [message(2, runAt(2)), message(1)];
  const old = await standIn(population, { legacy: true });
  try {
    await withScratch(async (scratch) => {
      const context = await poll(old.url, scratch);
      assert.deepEqual(shownIds(context), ["m-2", "m-1"], "a body was left out though nothing was excluded");
      assert.doesNotMatch(context, /queued for this session's channel/);
      const record = JSON.parse(fs.readFileSync(path.join(scratch, "aify-notify-seen-lead.json"), "utf8"));
      assert.deepEqual([record.namedSince, record.queued], [null, 0]);
    });
  } finally {
    old.server.close();
  }
});

test("an ordinary message behind any number of queued ones is shown on the first poll", async () => {
  // Review of 2df43e00: with 420 queued ahead of it, the hook stopped at its scan cap and never showed message 0.
  const population = [...Array.from({ length: 420 }, (_, n) => message(421 - n, runAt(421 - n))), message(0)];
  const { server, url } = await standIn(population);
  try {
    await withScratch(async (scratch) => {
      const context = await poll(url, scratch);
      assert.deepEqual(shownIds(context), ["m-0"]);
      assert.match(context, /420 unread message\(s\) are queued/);
      assert.equal(namedIds(context).length, 20, "the window names the twenty newest and counts the rest");
    });
  } finally {
    server.close();
  }
});

test("a run already named is not named again when newer ones leave", async () => {
  // Review of 2df43e00: a cue history of ids forgot the oldest, and they came back to the window once newer
  // messages left the projection, so all twenty were named a second time.
  const population = Array.from({ length: 20 }, (_, n) => message(20 - n, runAt(20 - n)));
  const { server, url } = await standIn(population);
  try {
    await withScratch(async (scratch) => {
      assert.equal(namedIds(await poll(url, scratch)).length, 20, "control: the old twenty");
      for (let batch = 0; batch < 10; batch += 1) {
        const newer = Array.from({ length: 20 }, (_, n) => message(100 + batch * 20 + n, runAt(100 + batch * 20 + n)));
        population.unshift(...newer.reverse());
        assert.equal(namedIds(await poll(url, scratch)).length, 20, `control: batch ${batch}`);
      }
      population.splice(0, 200); // the 200 newer ones are read: the old twenty are the newest window again
      assert.equal(population.length, 20, "control: only the old twenty remain");
      assert.equal(await poll(url, scratch), "", "a run was named twice");
    });
  } finally {
    server.close();
  }
});

test("a hook that overlaps a slower one names nothing, and the slower one's mark is not rolled back", async () => {
  // Review of 93de3c1d, reproduced with real hook processes: A walked a long inbox; B, ten seconds on, named run-new
  // and kept its mark; A finished later and wrote back the older mark it had read; C named run-new a second time.
  const ordinary = Array.from({ length: 100 }, (_, n) => message(1000 + n));
  const population = [message(1, runAt(1, "run-old")), ...ordinary];
  let started = () => {};
  const aStarted = new Promise((resolve) => { started = resolve; });
  const slow = await standIn(population, { delayMs: 250, onRequest: () => started() });
  const fast = await standIn(population);
  try {
    await withScratch(async (scratch) => {
      fs.writeFileSync(path.join(scratch, "aify-notify-seen-lead.json"), JSON.stringify(
        { session: "s-1", ids: ordinary.map((m) => m.id), namedSince: [stamp(1), "run-old"], queued: 1 }));
      const events = [];
      const a = poll(slow.url, scratch).then((context) => { events.push("A"); return context; });
      await aStarted;
      population.unshift(message(2, runAt(2, "run-new")));
      const b = await poll(fast.url, scratch);
      events.push("B");
      const aContext = await a;
      assert.deepEqual(events, ["B", "A"], "control: B ran and ended while A was still walking");
      const c = await poll(fast.url, scratch);
      const namings = [aContext, b, c].map((context) => namedIds(context).filter((id) => id === "m-2").length);
      assert.equal(namings.reduce((x, y) => x + y, 0), 1, `run-new was named ${JSON.stringify(namings)} times (A, B, C)`);
    });
  } finally {
    slow.server.close();
    fast.server.close();
  }
});

test("a hook whose lock was taken over while it walked prints nothing and leaves the record alone", async () => {
  // The recovery case: an old lock is taken over though its holder was only slow. The holder rereads its token before
  // printing and before writing. This proves it stops when it sees a foreign token by then, not that every resumed
  // holder is stopped: a pause after that check has no bound (notify-check.js, the recovery note).
  for (const takenOver of [false, true]) {
    const population = [message(1), ...Array.from({ length: 60 }, (_, n) => message(1000 + n))];
    let started = () => {};
    const firstRequest = new Promise((resolve) => { started = resolve; });
    const slow = await standIn(population, { delayMs: 200, onRequest: () => started() });
    try {
      await withScratch(async (scratch) => {
        const recordFile = path.join(scratch, "aify-notify-seen-lead.json");
        const before = JSON.stringify({ session: "s-1", ids: population.slice(1).map((m) => m.id), namedSince: null, queued: 0 });
        fs.writeFileSync(recordFile, before);
        const walk = poll(slow.url, scratch);
        await firstRequest;
        if (takenOver) fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), "999999:another-holder");
        const context = await walk;
        if (takenOver) {
          assert.equal(context, "", "a holder that lost its lock printed");
          assert.equal(fs.readFileSync(recordFile, "utf8"), before, "a holder that lost its lock wrote the record");
        } else {
          assert.match(context, /body of 1/, "control: the same walk, still holding its lock, prints");
        }
      });
    } finally {
      slow.server.close();
    }
  }
});

test("a hook whose lock was taken over writes no record even when it has nothing to print", async () => {
  // The write-only path: the queued count fell, so the record changes though nothing is printed.
  for (const takenOver of [false, true]) {
    const population = Array.from({ length: 60 }, (_, n) => message(1000 + n));
    let started = () => {};
    const firstRequest = new Promise((resolve) => { started = resolve; });
    const slow = await standIn(population, { delayMs: 200, onRequest: () => started() });
    try {
      await withScratch(async (scratch) => {
        const recordFile = path.join(scratch, "aify-notify-seen-lead.json");
        const before = JSON.stringify({ session: "s-1", ids: population.map((m) => m.id), namedSince: null, queued: 5 });
        fs.writeFileSync(recordFile, before);
        const walk = poll(slow.url, scratch);
        await firstRequest;
        if (takenOver) fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), "999999:another-holder");
        assert.equal(await walk, "", "control: there is nothing to print");
        const after = fs.readFileSync(recordFile, "utf8");
        if (takenOver) assert.equal(after, before, "a holder that lost its lock wrote the record");
        else assert.equal(JSON.parse(after).queued, 0, "control: the holder records the fall");
      });
    } finally {
      slow.server.close();
    }
  }
});

test("a lock left by a hook that has exited is taken over", async () => {
  const { server, url, urls } = await standIn([message(1)]);
  try {
    await withScratch(async (scratch) => {
      // A pid no process holds: the largest a 32-bit count allows, never handed out in a test run.
      fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), "2147483646");
      assert.match(await poll(url, scratch), /body of 1/, "a dead holder's lock silenced the agent");
      assert.ok(!fs.existsSync(path.join(scratch, "aify-notify-lead.lock")), "the lock outlived its holder");

      // CONTROL: a lock this test process holds is a live holder, and the hook stands aside.
      fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), String(process.pid));
      fs.rmSync(path.join(scratch, "aify-notify-seen-lead.json"), { force: true });
      urls.length = 0;
      assert.equal(await poll(url, scratch), "", "the hook ran beside a live holder");
      assert.deepEqual(urls, [], "a hook standing aside still read the inbox");

      // A fresh lock with no token yet (created, not written) or an unreadable one is an unknown holder: stand aside.
      // A pid the probe rejects (past the 32-bit range: ERR_INVALID_ARG_TYPE, not ESRCH) is unknown too.
      for (const content of ["", "not-a-pid", "2147483648:token"]) {
        fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), content);
        urls.length = 0;
        assert.equal(await poll(url, scratch), "", `a fresh lock holding ${JSON.stringify(content)} was taken over`);
        assert.deepEqual(urls, [], `a hook beside a fresh lock holding ${JSON.stringify(content)} read the inbox`);
      }
      // A lock it cannot read (here a directory: EISDIR) is unknown, not gone.
      fs.rmSync(path.join(scratch, "aify-notify-lead.lock"), { force: true });
      fs.mkdirSync(path.join(scratch, "aify-notify-lead.lock"));
      urls.length = 0;
      assert.equal(await poll(url, scratch), "", "an unreadable lock was taken over");
      assert.deepEqual(urls, [], "a hook beside an unreadable lock read the inbox");
      fs.rmSync(path.join(scratch, "aify-notify-lead.lock"), { recursive: true, force: true });
      fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), "not-a-pid");

      // ...until it is older than any walk, when it is taken over like any other.
      const aged = new Date(Date.now() - 10 * 60_000);
      fs.utimesSync(path.join(scratch, "aify-notify-lead.lock"), aged, aged);
      assert.match(await poll(url, scratch), /body of 1/, "an old unreadable lock silenced the agent");
      fs.writeFileSync(path.join(scratch, "aify-notify-lead.lock"), String(process.pid));
      fs.rmSync(path.join(scratch, "aify-notify-seen-lead.json"), { force: true });

      // A live pid on a lock older than LOCK_STALE_MS: a reused pid, or a holder that hung, is taken over. If that
      // holder resumes, only its best-effort ownership check stands in its way (the test above).
      const old = new Date(Date.now() - 10 * 60_000);
      fs.utimesSync(path.join(scratch, "aify-notify-lead.lock"), old, old);
      assert.match(await poll(url, scratch), /body of 1/, "an ancient lock silenced the agent");
    });
  } finally {
    server.close();
  }
});

test("queued work no run can name is still counted: a batched message, a requeue, a lower id at one instant", async () => {
  const population = [message(1, runAt(1, "run-m"))];
  const { server, url } = await standIn(population);
  try {
    await withScratch(async (scratch) => {
      assert.deepEqual(namedIds(await poll(url, scratch)), ["m-1"], "control");
      const counted = async (label) => {
        const context = await poll(url, scratch);
        assert.match(context, new RegExp(`${population.filter((m) => m.run).length} unread message\\(s\\) are queued`), label);
        assert.match(context, /None is named here/, label);
      };
      population.unshift(message(2, runAt(1, "run-m"))); // batched into the run already named
      await counted("a message batched into a named run");
      population.unshift(message(3, runAt(1, "run-a"))); // the same instant, an id ordering before the mark
      await counted("a later run at the same instant with a lower id");
      assert.equal(await poll(url, scratch), "", "control: nothing new, nothing printed");

      population.splice(0, 2); // two leave, then the count rises again to a number it was before
      assert.equal(await poll(url, scratch), "", "a fall is not news");
      population.unshift(message(4, runAt(1, "run-m")));
      await counted("a rise back to an earlier count");
    });
  } finally {
    server.close();
  }
});

test("the pure pieces: who opts in, the mark, what a cue says", async () => {
  const { channelSessionOptsIn, collectUnseen, cueMarkForSession, nextCue, queuedCueText, seenRecord } = await import("../notify-notice.mjs");
  const page = (queuedForChannel) => async () => ({ total: 0, unreadTotal: 2, messages: [], queuedForChannel });
  assert.equal((await collectUnseen(page({ total: 2 }), [])).queued, null, "a window with no list is no window");
  assert.equal((await collectUnseen(page({ total: 2, newest: [] }), [])).queued.total, 2, "control: a well-formed window");
  assert.equal(channelSessionOptsIn({ AIFY_RUNTIME: "claude-code", AIFY_CHANNEL_ROUTE: "dev" }), true);
  assert.equal(channelSessionOptsIn({ AIFY_RUNTIME: "claude-code", AIFY_CHANNEL_ROUTE: "x" }), false, "an unknown route");
  assert.equal(channelSessionOptsIn({ AIFY_CHANNEL_ROUTE: "plugin" }), false, "no runtime named");

  const mark = { namedSince: ["t1", "r1"], queued: 3 };
  assert.deepEqual(cueMarkForSession(seenRecord("s1", ["a"], mark), "s1"), mark);
  assert.deepEqual(cueMarkForSession(seenRecord("s1", ["a"], mark), "s2"), { namedSince: null, queued: 0 }, "another session");
  assert.deepEqual(cueMarkForSession({ session: "s1", ids: ["a"], cued: ["q"] }, "s1"), { namedSince: null, queued: 0 },
    "a 2df43e00 record");

  const window = { total: 3, newest: [
    { messageId: "c", requestedAt: "t2", runId: "r9" }, { messageId: "b", requestedAt: "t2", runId: "r1" },
    { messageId: "a", requestedAt: "t1", runId: "r5" },
  ] };
  assert.deepEqual(nextCue(window, { namedSince: null, queued: 0 }),
    { due: true, named: window.newest, mark: { namedSince: ["t2", "r9"], queued: 3 } });
  assert.deepEqual(nextCue(window, { namedSince: ["t2", "r1"], queued: 3 }).named.map((e) => e.messageId), ["c"]);
  assert.deepEqual(nextCue(window, { namedSince: ["t2", "r9"], queued: 3 }).due, false);
  assert.deepEqual(nextCue(window, { namedSince: ["t2", "r9"], queued: 2 }).due, true, "more queued than told");
  assert.deepEqual(nextCue(null, mark), { due: false, named: [], mark }, "a service that sent no window");

  const cue = queuedCueText({ named: [{ messageId: "m-1", from: "p", body: "secret body" }], queuedTotal: 5, unreadTotal: 7, agentId: "me" });
  assert.doesNotMatch(cue, /secret body/);
  assert.match(cue, /^aify-comms: 5 unread .*\(7 unread in this agent's whole inbox\)\. Newly queued, 1 named here:\n- from p, MessageId: m-1\n/s);
});
