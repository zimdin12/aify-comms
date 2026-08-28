// Every destructive control asks before it acts.
//
// THE INCIDENT THIS PROTECTS. A sweep of the dashboard's buttons fired real Stop controls and killed
// three managed workers mid-task. A destructive control that acts on the first click is one misclick
// from that outcome, and the operator has no undo for any of these: a removed agent, a deleted
// session, a deleted shared file and an unsent message are all gone.
//
// MEASURED 2026-08-28: six destructive controls, all six already behind `uiConfirm`. Nothing was
// broken -- this is a ratchet on a property that holds today, because the cost of losing it is a
// worker the operator did not mean to stop, and the cost of keeping it is one call.
//
// THE HANDLER IS ONE HOP FROM THE DISPATCH SITE, and a check that looked only near the dispatch
// found nothing guarded -- all six read as unconfirmed. `click-dispatch.mjs` routes, the handler
// lives in another module, and for `data-file-delete` the confirm is one hop further still:
// `deleteSharedFileFromRow` delegates to `deleteSharedFile`, which is where the prompt is. So this
// follows the call one level past the handler, and says so rather than pretending the first look
// was enough.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** Product sources only: the extraction fixture contains a whole copy of the old app.js. */
function productSources() {
  return fs.readdirSync(HERE)
    .filter((name) => (name.endsWith(".mjs") || name.endsWith(".js")) && !name.includes(".test."))
    .map((name) => [name, fs.readFileSync(path.join(HERE, name), "utf8")]);
}

/**
 * The controls whose click destroys something the operator cannot get back.
 *
 * Listed rather than pattern-matched: "destructive" is a judgement about consequences, not a fact
 * about a name. `data-msg-unsend` looks mild and removes a message from someone else's inbox;
 * `data-env-roots-reset` reads alarming and only clears a local override. A regex over verbs would
 * get both wrong, so each entry is a decision with its consequence written next to it.
 */
const DESTRUCTIVE = [
  ["data-agent-stop-worker", "stops a running managed worker mid-task"],
  ["data-agent-remove", "removes the agent and its history from the roster"],
  ["data-agent-delete-session", "deletes a session record"],
  ["data-channel-remove-member", "removes another agent from a channel"],
  ["data-file-delete", "deletes a shared file for everyone"],
];

/** The handler a dispatch line calls for a given attribute, e.g. `stopAgentWorker`. */
function handlerFor(attribute, dispatcher) {
  // NO CONSTRUCTED REGEXES. Building one from a template literal means every backslash must survive
  // being written twice, and here it did not: the character classes collapsed and the engine
  // complained about a range, on a test whose subject is destructive buttons. Plain string search
  // plus literal patterns cannot lose an escape in transit.
  const at = dispatcher.indexOf("[" + attribute + "]");
  if (at === -1) return null;
  const window = dispatcher.slice(at, at + 260);
  // The dispatcher writes both shapes: `handler(el.dataset.prop)` passes the value and `handler(el)`
  // passes the element for the handler to read itself. Reading only the first reported
  // `data-file-delete` as unrouted, which looks exactly like a dead control.
  for (const call of window.matchAll(/\b([A-Za-z_]\w*)\s*\(/g)) {
    const name = call[1];
    if (name === "closest" || name === "if" || name === "return") continue;
    return name;
  }
  return null;
}

function functionBody(name, sources) {
  for (const [file, text] of sources) {
    const start = new RegExp(`(?:export\\s+)?(?:async\\s+)?function\\s+${name}\\b`).exec(text);
    if (!start) continue;
    const rest = text.slice(start.index);
    const end = rest.indexOf(`\n}`);
    return { file, body: end === -1 ? rest : rest.slice(0, end) };
  }
  return null;
}

/** Does this handler prompt, itself or in something it calls one hop away? */
function asksFirst(name, sources) {
  const found = functionBody(name, sources);
  if (!found) return { ok: false, why: `no handler named ${name} in any product module` };
  if (found.body.includes("uiConfirm(")) return { ok: true, where: `${name} in ${found.file}` };
  for (const callee of found.body.matchAll(/\b([a-z]\w+)\s*\(/g)) {
    if (callee[1] === name) continue;
    const nested = functionBody(callee[1], sources);
    if (nested && nested.body.includes("uiConfirm(")) {
      return { ok: true, where: `${name} -> ${callee[1]} in ${nested.file}` };
    }
  }
  return { ok: false, why: `${name} (${found.file}) reaches no uiConfirm` };
}

test("the dispatcher and the confirm helper are both present", () => {
  // Controls. An unreadable dispatcher yields no handlers and every case below turns vacuous; a
  // dashboard with no uiConfirm at all would make "reaches no uiConfirm" the honest answer for
  // everything rather than a finding.
  const sources = productSources();
  assert.ok(sources.length > 30, `only ${sources.length} product modules found`);
  const dispatcher = sources.find(([name]) => name === "click-dispatch.mjs");
  assert.ok(dispatcher, "click-dispatch.mjs is missing");
  const confirms = sources.reduce((n, [, text]) => n + (text.match(/uiConfirm\(/g) || []).length, 0);
  assert.ok(confirms >= 10, `only ${confirms} uiConfirm call sites; the helper may have been renamed`);
});

test("every destructive control resolves to a handler", () => {
  // Separate from the confirm assertion on purpose: "no handler found" and "handler does not ask"
  // are different failures, and reporting the first as the second sends the reader to the wrong file.
  const sources = productSources();
  const dispatcher = sources.find(([name]) => name === "click-dispatch.mjs")[1];
  for (const [attribute] of DESTRUCTIVE) {
    assert.ok(
      handlerFor(attribute, dispatcher),
      `${attribute} is not routed to a handler in click-dispatch.mjs — either it is dead, or this `
        + "scan no longer understands how the dispatcher is written",
    );
  }
});

for (const [attribute, consequence] of DESTRUCTIVE) {
  test(`${attribute} asks before it ${consequence}`, () => {
    const sources = productSources();
    const dispatcher = sources.find(([name]) => name === "click-dispatch.mjs")[1];
    const handler = handlerFor(attribute, dispatcher);
    const verdict = asksFirst(handler, sources);
    assert.ok(verdict.ok, `${attribute} acts on the first click: ${verdict.why}`);
  });
}

test("the check can tell a guarded handler from an unguarded one", () => {
  // The negative control. Every case above passes if `asksFirst` returns true for anything, and a
  // ratchet that cannot fail is decoration.
  const sources = productSources();
  const unguarded = asksFirst("loadFiles", sources);
  assert.equal(unguarded.ok, false, "a handler with no prompt was reported as guarded");
});
