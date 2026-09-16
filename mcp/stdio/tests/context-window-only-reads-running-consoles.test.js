// `context-window` measures RUNNING consoles, and a stopped agent is not an unreadable one.
//
// EXTERNAL REVIEW, 2026-09-16: "Doctor's context-window check couldn't read any of the 24 consoles."
// Measured on this host the same day: all 21 agents the check picked were offline or stopped. The
// agent listing reports `consoleAvailable` for EVERY managed agent -- it is `sessionMode != resident`,
// not "a terminal is running" -- and the console route answers such an agent with `live: false` and,
// at most, the recorded tail of a terminal that ended. The check read that as "could not read", so a
// fleet with nothing running reported `unknown-all`, and one stopped agent's old footer
// (147.9k/272k, from a terminal that had exited) was measured as if it were current.
//
// An exhausted agent is still RUNNING -- it reads online, takes work and cannot do it -- so a stopped
// console holds nothing this check exists to find.

import assert from "node:assert/strict";
import test from "node:test";

import { checkContextWindow } from "../context-window-check.mjs";

const FULL = "  │ 922.4k/900k │ [██████████] 100% │ 3m 39s";
const LOW = " ready | gpt 5.6 sol 900k | 90k/900k | 1h 2m | cmp 0";

function harness(agents, consoles) {
  const added = [];
  const fetched = [];
  return {
    added,
    fetched,
    deps: {
      get: async (path) => {
        fetched.push(path);
        if (path === "/api/v1/agents") return { agents };
        const match = /\/api\/v1\/agents\/([^/]+)\/console/.exec(path);
        return match ? consoles[decodeURIComponent(match[1])] ?? null : null;
      },
      add: (...args) => { added.push(args); return args; },
      skip: () => {},
    },
  };
}

const managed = (runtime = "hermes") => ({ consoleAvailable: true, sessionMode: "managed", runtime });
// The two shapes the console route really returns for an agent with no running terminal.
const stoppedWithTail = (output) => ({ ok: true, live: false, historical: true, status: "exited", output });
const neverRan = { ok: true, live: false, historical: false };

test("a fleet with nothing running is NOT 'none of N could be read'", async () => {
  const h = harness(
    { a: managed(), b: managed("claude-code"), c: managed("codex") },
    { a: stoppedWithTail(""), b: stoppedWithTail(""), c: neverRan },
  );
  await checkContextWindow(h.deps);
  const [, ok, code, detail] = h.added[0];
  assert.notEqual(code, "unknown-all", "stopped consoles were reported as unreadable");
  assert.equal(code, "none");
  assert.equal(ok, true);
  assert.match(detail, /3 .*not running/, "the stopped agents were not accounted for");
});

test("a STOPPED agent's recorded footer is history, not a measurement", async () => {
  // The terminal ended; whatever its last screen said is not the state of anything running.
  const h = harness({ gone: managed() }, { gone: stoppedWithTail(FULL) });
  await checkContextWindow(h.deps);
  assert.notEqual(h.added[0][2], "exhausted", "an exited terminal's last footer was reported as a live agent");
});

test("CONTROL: the same footer on a RUNNING console is still found", async () => {
  // Without this, a check that ignored every console would pass both tests above.
  const h = harness({ busy: managed() }, { busy: { ok: true, live: true, output: FULL } });
  await checkContextWindow(h.deps);
  assert.equal(h.added[0][2], "exhausted");
});

test("stopped agents do not take the fan-out slots a running one needs", async () => {
  // Sorted alphabetically, the stopped `a*` agents come first. Counting them against the cap left the
  // running agent at the end unopened -- and it is the one that is full.
  const agents = { a1: managed(), a2: managed(), a3: managed(), zz: managed() };
  const consoles = { a1: stoppedWithTail(""), a2: neverRan, a3: stoppedWithTail(""), zz: { ok: true, live: true, output: FULL } };
  const h = harness(agents, consoles);
  await checkContextWindow({ ...h.deps, maxConsoles: 1 });
  assert.equal(h.added[0][2], "exhausted", "the one running console was never opened");
});

test("a fleet of STOPPED agents is still bounded, and the unopened tail is disclosed", async () => {
  // Not counting stopped consoles against the cap must not mean opening every one of them.
  const agents = {};
  const consoles = {};
  for (let i = 0; i < 200; i += 1) {
    agents[`s${String(i).padStart(3, "0")}`] = managed();
    consoles[`s${String(i).padStart(3, "0")}`] = neverRan;
  }
  const h = harness(agents, consoles);
  await checkContextWindow({ ...h.deps, maxConsoles: 5, maxOpened: 10 });
  assert.equal(h.fetched.filter((p) => p.includes("/console")).length, 10);
  const [, ok, code] = h.added[0];
  assert.equal(ok, false, "190 unopened agents were reported as a clean result");
  assert.equal(code, "partial");
});

test("a console that did not answer is still UNREADABLE, not 'not running'", async () => {
  // Only the route's own `live: false` says nothing runs. A failed fetch says nothing at all.
  const h = harness({ quiet: managed() }, {});
  await checkContextWindow(h.deps);
  assert.equal(h.added[0][2], "unknown-all");
});

test("an unreadable RUNNING console names its runtime, so the operator knows what could not be read", async () => {
  const h = harness(
    { x: managed("claude-code"), y: managed("claude-code"), z: managed("codex") },
    {
      x: { ok: true, live: true, output: "> working" },
      y: { ok: true, live: true, output: "> idle" },
      z: { ok: true, live: true, output: "? for shortcuts" },
    },
  );
  await checkContextWindow(h.deps);
  const [, ok, code, detail] = h.added[0];
  assert.equal(ok, false);
  assert.equal(code, "unknown-all");
  assert.match(detail, /claude-code ×2/);
  assert.match(detail, /codex ×1/);
});

test("a healthy running fleet beside stopped agents is ok, and says how many were not running", async () => {
  const h = harness({ live: managed(), off: managed() }, { live: { ok: true, live: true, output: LOW }, off: neverRan });
  await checkContextWindow(h.deps);
  const [, ok, code, detail] = h.added[0];
  assert.equal(ok, true);
  assert.equal(code, "ok");
  assert.match(detail, /1 managed agent\(s\) not running/);
});
