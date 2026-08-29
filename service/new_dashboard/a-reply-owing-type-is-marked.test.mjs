// A message type that OWES A REPLY must be visually distinguishable in the chat rail.
//
// The badge palette already encodes a rule nobody had written down: of the six message types, the
// dashboard gave `request` a colour and `review` a colour and left the other four in the default
// grey. Those two are not an arbitrary pair -- they are two of the THREE types the service treats as
// owing an answer. `service/api_core/reply_expectation.py` is where that set lives:
//
//     def _message_type_expects_reply(message_type: str) -> bool:
//         return (message_type or "").strip().lower() in {"request", "review", "error"}
//
// `error` was the third, and it rendered identically to an `info`. MEASURED on the operator's live
// database, 2026-08-29: 544 `error` messages of 34,107, 42 of them in the last seven days -- and
// every one of the 49 auto-mirrored dispatch-failure notices the reconciler mails is one of them.
// The message that says a run failed looked like the message that says hello.
//
// DERIVED FROM THE PYTHON, NOT LISTED HERE. A fourth reply-owing type added tomorrow fails this the
// day it lands rather than the day somebody remembers a stylesheet exists. The direction is
// deliberate: types that do NOT owe a reply are free to stay unstyled, because "everything is
// coloured" is a palette, not a signal.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const REPLY_EXPECTATION = join(REPO, "service", "api_core", "reply_expectation.py");
const STYLES = join(HERE, "styles.css");

// Pure, so both controls below can drive them with known inputs.
export function replyOwingTypes(pythonSource) {
  const match = pythonSource.match(/_message_type_expects_reply[\s\S]*?in\s*\{([^}]*)\}/);
  if (!match) return [];
  return [...match[1].matchAll(/"([a-z-]+)"|'([a-z-]+)'/g)]
    .map((m) => m[1] || m[2])
    .sort();
}

export function styledTypes(css) {
  return [...css.matchAll(/\.msg-badge\.t-([a-z-]+)\s*\{/g)].map((m) => m[1]).sort();
}

test("every type that owes a reply carries its own badge colour", () => {
  const owed = replyOwingTypes(readFileSync(REPLY_EXPECTATION, "utf8"));
  const styled = new Set(styledTypes(readFileSync(STYLES, "utf8")));
  const unmarked = owed.filter((type) => !styled.has(type));
  assert.deepEqual(unmarked, [], `these types owe a reply and render in the default badge grey: `
    + `${unmarked.join(", ")}. Add a \`.msg-badge.t-<type>\` rule to styles.css.`);
});

test("THE SCAN FINDS THE SET, rather than passing on an empty one", () => {
  // POSITIVE CONTROL. An empty `owed` satisfies the assertion above for the wrong reason, and this
  // whole file is worth nothing if its green can mean "the regex stopped matching".
  const owed = replyOwingTypes(readFileSync(REPLY_EXPECTATION, "utf8"));
  assert.ok(owed.length >= 3, `only found ${owed.length} reply-owing type(s); the reader has lost `
    + "track of the set in reply_expectation.py and its verdict is empty");
  assert.ok(owed.includes("error"), "the set no longer contains `error`, which is the type this "
    + "file was written for -- confirm that is deliberate before deleting the rule it guards");
});

test("THE SCAN CAN SAY NO", () => {
  // NEGATIVE CONTROL, on inputs written to fail. A checker only ever pointed at passing code has
  // never been shown to detect anything.
  const owed = replyOwingTypes('    return (t or "").strip().lower() in {"alpha", "beta"}\n'
    .replace("return", "_message_type_expects_reply\n    return"));
  assert.deepEqual(owed, ["alpha", "beta"]);
  const styled = new Set(styledTypes(".msg-badge.t-alpha { color: red; }"));
  assert.deepEqual(owed.filter((t) => !styled.has(t)), ["beta"]);
});

test("a type that owes nothing is allowed to stay unstyled", () => {
  // SCOPE. `response` and `info` are 76.7% of all messages between them; colouring them would make
  // the badge row decorative and cost the two that mean something. This pins that as a decision.
  const owed = new Set(replyOwingTypes(readFileSync(REPLY_EXPECTATION, "utf8")));
  assert.equal(owed.has("response"), false);
  assert.equal(owed.has("info"), false);
});
