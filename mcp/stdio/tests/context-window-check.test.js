// `context-window` — the check for an agent whose conversation has outgrown its model.
//
// THE SITUATION IT WAS BUILT FROM, measured 2026-08-31: five managed hermes agents silent for over
// two hours while status read `online`, `lastSeen` refreshed every few seconds, and dispatch runs
// reported `delivered`. Nothing in the control plane could say why, and the auto-mirrored failure
// notice offers four candidate causes without naming this one.
//
// THE SAMPLES BELOW ARE REAL, taken off the running fleet through `/api/v1/agents/{id}/console`.
// A parser proven only against text the author wrote is proven against their own assumptions; the
// mangled capture in particular is the case a hand-written sample would never have contained.

import assert from "node:assert/strict";
import test from "node:test";

import {
  CONTEXT_FULL_RATIO,
  CONTEXT_NEAR_RATIO,
  checkContextWindow,
  contextWindowVerdict,
  parseContextUsage,
} from "../context-window-check.mjs";

// A healthy footer, captured whole.
const HEALTHY = " ─ ready │ gpt 5.6 sol 900k │ 820.3k/900k │ [█████████░] 91% │ 16h 26m │ ✓ 3h 13m │ cmp 5 │ voice off │ 1 session";

// THE ONE THAT MATTERS: a dying agent's screen, wrapped and shredded. Its bar and percentage are
// broken across visual rows; the `922.4k/900k` pair survives because it is short enough to fit
// between wraps. This is why the ratio is computed from the pair and not read off the glyph.
const MANGLED = [
  " ┊  Context length exceeded (1,122,638 tokens). Cannot compress further.",
  "                       900k │ 922.4k/900k │ [██████████] 100% │ 3m 39s │ ✓ 0s │ voice off │ 1 sessio",
  "n                                                                  50      10s │ voice o f │ 1 se si",
].join("\n");

// A smaller window, over its own ceiling. Integers, no decimal point.
const OVER = "  │ 323k/272k │ [██████████] 100% │ 20h 13m │ ✓ 3h 35m │ cmp 7 │ voice off";

// ── parseContextUsage ───────────────────────────────────────────────────────────────────────────

test("it reads the pair off a real healthy footer", () => {
  const usage = parseContextUsage(HEALTHY);
  assert.equal(usage.usedTokens, 820300);
  assert.equal(usage.windowTokens, 900000);
  assert.ok(Math.abs(usage.ratio - 0.9114) < 0.001);
});

test("it reads a MANGLED screen, where the bar and the percentage are destroyed", () => {
  // The whole reason the rendered percent is ignored. This capture is what the check will actually
  // be handed on the day it matters.
  const usage = parseContextUsage(MANGLED);
  assert.equal(usage.usedTokens, 922400);
  assert.equal(usage.windowTokens, 900000);
  assert.ok(usage.ratio > 1);
});

test("it reads integers with no decimal point", () => {
  const usage = parseContextUsage(OVER);
  assert.equal(usage.usedTokens, 323000);
  assert.equal(usage.windowTokens, 272000);
});

test("the LAST pair wins, because the footer is the bottom line", () => {
  // Scrollback above it can carry anything, including an agent quoting a figure like this back at
  // itself -- which is exactly what a reviewer discussing this check would produce.
  const scrollback = `an agent wrote "100k/200k" earlier in the transcript\n${HEALTHY}`;
  assert.equal(parseContextUsage(scrollback).usedTokens, 820300);
});

test("an unreadable console is NULL, never zero", () => {
  // Null and 0% are different facts. Reporting a console we could not read as "0% used" is the false
  // green this tool exists to refuse.
  for (const value of ["", null, undefined, "no footer here", "  │ ready │ voice off │"]) {
    assert.equal(parseContextUsage(value), null);
  }
});

test("a zero or absent window is unreadable, not a division", () => {
  // `x/0` is Infinity and would render as a confident verdict about a meaningless number.
  assert.equal(parseContextUsage("│ 300k/0k │"), null);
});

// ── contextWindowVerdict ────────────────────────────────────────────────────────────────────────

const at = (agentId, ratio) => ({ agentId, usage: { ratio } });

test("a full window FAILS and names the agents", () => {
  const verdict = contextWindowVerdict([at("comms-senior-dev", 1.02), at("graph-senior-dev", 0.5)]);
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "exhausted");
  assert.match(verdict.detail, /comms-senior-dev 102%/);
  assert.doesNotMatch(verdict.detail, /graph-senior-dev/, "a healthy agent was named as exhausted");
});

test("the fix says a Reset DISCARDS history, because that is a decision and not a repair", () => {
  const verdict = contextWindowVerdict([at("a", 1.5)]);
  assert.match(verdict.fix, /discards/);
});

test("NEAR full fails too, so the operator can choose the moment", () => {
  // The alternative is being told at 100%, when the agent is already unable to answer and the choice
  // is gone.
  const verdict = contextWindowVerdict([at("graph-senior-dev", 0.91)]);
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "near-full");
});

test("the calibration point: 0.91 is NEAR, and it is the reading of an agent that still worked", () => {
  // ANTI-ARBITRARY THRESHOLD. graph-senior-dev sat at 0.91 and produced the fleet's most recent
  // output while two agents at 1.02 and 1.19 produced nothing for hours. So the boundary sits below
  // every observed failure and above the one observed success -- and if that ordering is ever
  // inverted, this test says so.
  assert.ok(CONTEXT_NEAR_RATIO <= 0.91, "the near threshold rose above a reading that still worked");
  assert.ok(CONTEXT_FULL_RATIO > 0.91, "an agent that was still producing would be reported exhausted");
});

test("EXHAUSTED outranks NEAR, so the urgent half is what the operator reads", () => {
  const verdict = contextWindowVerdict([at("near", 0.95), at("dead", 1.4)]);
  assert.equal(verdict.code, "exhausted");
});

test("a healthy fleet passes and reports the WORST reading, not an average", () => {
  const verdict = contextWindowVerdict([at("a", 0.1), at("b", 0.62)]);
  assert.equal(verdict.ok, true);
  assert.match(verdict.detail, /worst b 62%/);
});

test("consoles that could not be read are COUNTED in a pass, never silently dropped", () => {
  // Otherwise a fleet where nine of ten consoles failed reads identically to one where all ten were
  // healthy.
  const verdict = contextWindowVerdict([at("a", 0.2), { agentId: "b", usage: null }]);
  assert.equal(verdict.ok, true);
  assert.match(verdict.detail, /1 could not be read/);
});

test("NO readable console at all is unknown-all, not a pass", () => {
  // "No evidence is not a pass" -- the rule this repo wrote after a doctor check reported green
  // twice while measuring nothing.
  const verdict = contextWindowVerdict([{ agentId: "a", usage: null }, { agentId: "b", usage: null }]);
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "unknown-all");
  assert.match(verdict.detail, /none of 2/);
});

test("no consoles at all is an honest pass, and says so", () => {
  const verdict = contextWindowVerdict([]);
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "none");
});

// ── the CHECK, not just its verdict ─────────────────────────────────────────────────────────────
//
// A predicate proven in isolation leaves the call to it unproven, and that is precisely where this
// repo's `service` check failed once: an early return answered the case itself and never consulted
// the verdict it had computed.

function harness(agents, consoles = {}) {
  const calls = { added: [], skipped: [], fetched: [] };
  return {
    calls,
    deps: {
      get: async (path) => {
        calls.fetched.push(path);
        if (path === "/api/v1/agents") return agents;
        const match = /\/api\/v1\/agents\/([^/]+)\/console/.exec(path);
        if (match) return consoles[decodeURIComponent(match[1])] ?? null;
        return null;
      },
      add: (...args) => { calls.added.push(args); return args; },
      skip: (...args) => { calls.skipped.push(args); return args; },
    },
  };
}

const managed = (consoleAvailable = true) => ({ consoleAvailable, sessionMode: "managed" });

test("the check reads each managed console and reports the exhausted one", () => {
  const { deps, calls } = harness(
    { agents: { dead: managed(), fine: managed() } },
    { dead: { output: MANGLED }, fine: { output: HEALTHY } },
  );
  return checkContextWindow(deps).then(() => {
    const [id, ok, code, detail] = calls.added[0];
    assert.equal(id, "context-window");
    assert.equal(ok, false);
    assert.equal(code, "exhausted");
    assert.match(detail, /dead/);
  });
});

test("an agent with NO console is not fetched at all", () => {
  // Fetching one per registered agent would make the doctor slower than what it is diagnosing, and
  // there is nothing to read on an agent that has no console.
  const { deps, calls } = harness({ agents: { nope: managed(false), yes: managed() } }, { yes: { output: HEALTHY } });
  return checkContextWindow(deps).then(() => {
    assert.ok(!calls.fetched.some((p) => p.includes("nope")), "a console-less agent was fetched anyway");
    assert.ok(calls.fetched.some((p) => p.includes("yes")));
  });
});

test("a RESIDENT agent is not measured — this reads managed consoles", () => {
  const { deps, calls } = harness({ agents: { me: { consoleAvailable: true, sessionMode: "resident" } } });
  return checkContextWindow(deps).then(() => {
    assert.ok(!calls.fetched.some((p) => p.includes("/console")));
    assert.equal(calls.added[0][2], "none");
  });
});

test("a service that does not answer is UNKNOWN, not a pass", () => {
  const { deps, calls } = harness(null);
  return checkContextWindow(deps).then(() => {
    const [, ok, code] = calls.added[0];
    assert.equal(ok, false);
    assert.equal(code, "unknown");
  });
});

test("the console fan-out is BOUNDED, so a large fleet cannot stall the doctor", () => {
  const agents = { agents: {} };
  for (let i = 0; i < 40; i += 1) agents.agents[`a${i}`] = managed();
  const { deps, calls } = harness(agents);
  return checkContextWindow({ ...deps, maxConsoles: 5 }).then(() => {
    assert.equal(calls.fetched.filter((p) => p.includes("/console")).length, 5);
  });
});

test("the fix does not recommend a Reset without saying which bridge makes it work", () => {
  // A doctor that advises a remedy known not to work is worse than one that says nothing. Before
  // ea18156b the hermes launch ignored the fresh-context policy entirely: `comms_restart
  // freshContext=true` reported success and resumed the same conversation. An operator following
  // this advice on an older bridge would press it, see nothing change, and distrust the check.
  const verdict = contextWindowVerdict([at("a", 1.5)]);
  assert.match(verdict.fix, /ea18156b/);
  assert.match(verdict.fix, /bridge-current/);
});

// ── the fan-out cap must never produce a clean row ──────────────────────────────────────────────
//
// REVIEWER FINDING, 2026-08-31. The cap took the first N candidates in insertion order and the
// verdict never learned it had been capped, so an exhausted agent at position N+1 yielded `ok`. That
// is the precise false green this check was written to abolish, reintroduced by its own bound.

test("a capped fan-out reports PARTIAL, never ok, even when everything measured is healthy", () => {
  const verdict = contextWindowVerdict([at("a", 0.2)], { unmeasured: 7 });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "partial");
  assert.match(verdict.detail, /7 more were not opened/);
});

test("a capped fan-out that measured NOTHING is partial too, not 'no agent to measure'", () => {
  const verdict = contextWindowVerdict([], { unmeasured: 3 });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "partial");
});

test("an EXHAUSTED agent still outranks the cap — the urgent answer is not hidden by partial", () => {
  const verdict = contextWindowVerdict([at("dead", 1.4)], { unmeasured: 9 });
  assert.equal(verdict.code, "exhausted");
});

test("with nothing left over, a healthy fleet is still a clean ok", () => {
  // Anti-vacuity: the fix must not turn every pass into partial.
  assert.equal(contextWindowVerdict([at("a", 0.2)], { unmeasured: 0 }).code, "ok");
});

test("THE CHECK reports partial when more agents were eligible than the cap allows", () => {
  const agents = { agents: {} };
  for (let i = 0; i < 9; i += 1) agents.agents[`a${i}`] = managed();
  // NOT `HEALTHY` -- that fixture is 820.3k/900k = 91%, which is near-full by this module's own
  // calibration, and near-full outranks partial. The first version of this test used it and failed
  // for that reason, which is the fixture being wrong rather than the code.
  const LOW = " ready | gpt 5.6 sol 900k | 90k/900k | 1h 2m | cmp 0 | voice off";
  const { deps, calls } = harness(agents, Object.fromEntries(
    Array.from({ length: 9 }, (_, i) => [`a${i}`, { output: LOW }])));
  return checkContextWindow({ ...deps, maxConsoles: 4 }).then(() => {
    const [, ok, code, detail] = calls.added[0];
    assert.equal(ok, false);
    assert.equal(code, "partial");
    assert.match(detail, /5 more were not opened/);
  });
});

test("the capped selection is DETERMINISTIC, so two runs measure the same agents", () => {
  // Insertion order made this a lottery: which agents got looked at depended on whatever the service
  // returned first. Alphabetical is not risk-ranked, but it is reproducible, and the truncation is
  // reported either way.
  const agents = { agents: { zeta: managed(), alpha: managed(), mid: managed() } };
  const consoles = { alpha: { output: HEALTHY }, mid: { output: HEALTHY }, zeta: { output: HEALTHY } };
  const { deps, calls } = harness(agents, consoles);
  return checkContextWindow({ ...deps, maxConsoles: 2 }).then(() => {
    const opened = calls.fetched.filter((p) => p.includes("/console"));
    assert.ok(opened[0].includes("alpha") && opened[1].includes("mid"),
              `the cap did not take the first two alphabetically: ${opened.join(", ")}`);
  });
});

test("truncation is disclosed even when an EXHAUSTED agent is found", () => {
  // The stronger form of the reviewer's rule. `exhausted` outranks `partial` and should -- a measured
  // problem beats an unmeasured maybe -- but an operator reading it must still learn the tail was
  // never opened, or the cap is disclosed only on the quiet path, exactly when it matters least.
  const verdict = contextWindowVerdict([at("dead", 1.4)], { unmeasured: 9 });
  assert.equal(verdict.code, "exhausted");
  assert.match(verdict.detail, /9 further console\(s\) were not opened/);
});

test("truncation is disclosed on the NEAR-FULL path too", () => {
  const verdict = contextWindowVerdict([at("close", 0.95)], { unmeasured: 4 });
  assert.equal(verdict.code, "near-full");
  assert.match(verdict.detail, /4 further console\(s\) were not opened/);
});

test("with nothing skipped, no verdict grows a truncation clause", () => {
  // Anti-vacuity for the two above.
  const verdict = contextWindowVerdict([at("dead", 1.4)], { unmeasured: 0 });
  assert.doesNotMatch(verdict.detail, /not opened/);
});
