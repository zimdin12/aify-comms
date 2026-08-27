// The line addressed to the agent says as much as the table above it.
//
// THE DEFECT. `comms_usage` prints a pool table and then one line about the caller's own pool. The
// table's formatter shows BOTH windows and the severity whenever it is not "normal". The personal
// line -- ten lines later in the same file, and the one an agent actually acts on -- showed the
// weekly figure and a flag that fires only on `critical`. The middle state was invisible on it.
//
// MEASURED on the live fleet 2026-08-27: of 47 agents, 21 carried `poolSeverity: "warning"` and every
// one of them carried `poolWeeklyPctLeft: 16`. Severity is the WORSE of the five-hour and weekly
// windows (`usage_openai.py`: worst = max(five_hour, weekly); warning at >= 90% used), so those
// agents were told "16% weekly left" while the window near its limit was the five-hour one. Both
// numbers were correct. They answer different questions and only one was being asked.
//
// `poolSeverity` is serialised onto every agent row by `records.py` and, before this, was read by
// NOTHING in the repo -- a field computed on every request and dropped.
import assert from "node:assert/strict";
import test from "node:test";

import { personalQuotaLine } from "../usage-predicates.mjs";

const AGENT = "sc-coder";

test("the warning state reaches the line -- the case that was invisible", () => {
  // Verbatim from the live payload: 21 agents looked exactly like this.
  const line = personalQuotaLine(AGENT, {
    usageSource: "openai", poolWeeklyPctLeft: 16, poolSeverity: "warning", quotaCritical: false,
  });
  assert.match(line, /16% weekly left/);
  assert.match(line, /\[warning\]/, "the severity the service computed for this agent was dropped");
});

test("critical still shows, since severity subsumes the old boolean", () => {
  const line = personalQuotaLine(AGENT, {
    usageSource: "openai", poolWeeklyPctLeft: 1, poolSeverity: "critical", quotaCritical: true,
  });
  assert.match(line, /\[critical\]/);
});

test("normal adds no tag, so a healthy pool stays quiet", () => {
  // The reason the tag is conditional: a label on every line is a label nobody reads.
  const line = personalQuotaLine(AGENT, {
    usageSource: "anthropic", poolWeeklyPctLeft: 88, poolSeverity: "normal", quotaCritical: false,
  });
  assert.match(line, /88% weekly left/);
  assert.doesNotMatch(line, /\[/, `a normal pool was tagged: ${line}`);
});

test("an ABSENT percentage is '?', never 0%", () => {
  // This module's founding rule: a missing number rendered as zero reports an exhausted pool when
  // nothing is known about it. `null` and `undefined` both arrive here -- the field is omitted when
  // no pool is resolved, and null when the pool has no weekly window.
  for (const weekly of [null, undefined]) {
    const line = personalQuotaLine(AGENT, { usageSource: "openai", poolWeeklyPctLeft: weekly });
    assert.match(line, /\? weekly left/, `rendered ${weekly} as something other than "?"`);
    assert.doesNotMatch(line, /0% weekly left/);
  }
});

test("0% is a REAL reading and survives", () => {
  // The other half of unknown-vs-zero: a genuine zero must not be mistaken for missing and printed
  // as "?", or an actually-exhausted pool would read as unknown.
  const line = personalQuotaLine(AGENT, {
    usageSource: "openai", poolWeeklyPctLeft: 0, poolSeverity: "critical",
  });
  assert.match(line, /0% weekly left/);
  assert.doesNotMatch(line, /\? weekly left/);
});

test("no source, or no agent id, means no line at all", () => {
  // The line is a convenience on a best-effort lookup. Half a line -- an arrow pointing at nothing --
  // is worse than none, and the caller concatenates whatever comes back.
  assert.equal(personalQuotaLine(AGENT, { poolWeeklyPctLeft: 16, poolSeverity: "warning" }), "");
  assert.equal(personalQuotaLine("", { usageSource: "openai", poolWeeklyPctLeft: 16 }), "");
  assert.equal(personalQuotaLine(AGENT, {}), "");
});

test("it does not throw on what a failed lookup returns", () => {
  // The call site swallows lookup failures and passes on whatever it got; a throw here would cost
  // the whole usage report, which is the answer the caller asked for.
  for (const agent of [null, undefined, "", 0, [], { usageSource: 7 }]) {
    assert.equal(typeof personalQuotaLine(AGENT, agent), "string");
  }
});

test("a severity the service invents later is shown, not swallowed", () => {
  // Derived, not enumerated: anything that is not "normal" is worth showing. A hardcoded
  // {warning, critical} list would silently drop the next state somebody adds -- which is exactly
  // how `warning` came to be missing here.
  const line = personalQuotaLine(AGENT, {
    usageSource: "openai", poolWeeklyPctLeft: 5, poolSeverity: "exhausted",
  });
  assert.match(line, /\[exhausted\]/);
});

test("severity is matched case-insensitively and trimmed", () => {
  const line = personalQuotaLine(AGENT, {
    usageSource: "openai", poolWeeklyPctLeft: 16, poolSeverity: "  WARNING  ",
  });
  assert.match(line, /\[warning\]/);
});
