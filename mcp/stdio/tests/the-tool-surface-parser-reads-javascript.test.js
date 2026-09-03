// The tool-surface parser, proven on JavaScript rather than on today's tree.
//
// WHY THIS EXISTS. `tool-surface-ratchet.test.js` checks that the parser finds every `server.tool(`
// currently written, which is the control that catches a broken instrument -- but it only ever
// exercises the constructs that happen to be in the tree this week. Three times now the parser has
// lost registrations to a construct nobody had thought about: a quote inside a code comment, then
// backticks inside a regex, then a template literal nested in another template's `${…}` hole. Each
// was found only because somebody counted by hand.
//
// SO THE HARD CASES ARE HANDED TO IT DIRECTLY, EACH INSIDE A HANDLER BODY. That placement is the
// whole test and it was wrong on the first attempt: a construct written BETWEEN two registrations is
// never walked, because one span stops at its closing paren and the next starts after it. Every one
// of these tests passed against a parser with its regex handling switched off until they were moved
// inside the body -- green, and proving nothing. The live defect was a `.replace()` in the middle of
// a tool's handler, which is where a span walk actually has to survive.
//
// A REGISTRATION LOST IS A CEILING NOT HELD, which is the consequence that makes this worth a file:
// the ratchet's "no tool is over its ceiling" and "every tool has a ceiling" tests are both scoped
// to the tools the parser RETURNED, so a tool it cannot see grows for free and every test is green.

import { test } from "node:test";
import assert from "node:assert/strict";

import { registrationsIn } from "./tool-surface-size.mjs";

/** A registration whose handler contains `body` -- the code the span walk must survive. */
const source = (name, body = "") => `
  server.tool(
    "${name}",
    "A description.",
    { field: z.string().describe("A field.") },
    async () => {
      ${body}
      return { content: [] };
    },
  );
`;

/** The names found, given a hard construct inside the FIRST of two registrations. */
const acrossBody = (body) => registrationsIn(source("comms_first", body) + source("comms_second"))
  .map((t) => t.name);

test("THE PARSER FINDS A PLAIN REGISTRATION", () => {
  // POSITIVE CONTROL. Every test below asserts that some construct does NOT hide a registration,
  // and a parser that found nothing at all would fail them in a way easily misread as the construct
  // being at fault. This one says the instrument works before the hard cases ask what it misses.
  const found = registrationsIn(source("comms_example"));
  assert.deepEqual(found.map((t) => t.name), ["comms_example"]);
  assert.equal(found[0].description, "A description.".length);
  assert.equal(found[0].schema, "A field.".length);
});

test("AND FINDS NOTHING IN SOURCE THAT REGISTERS NOTHING", () => {
  // NEGATIVE CONTROL, the other half of the pair. A parser that reported a tool for any source
  // would satisfy every assertion in this file while measuring nothing real.
  assert.deepEqual(registrationsIn("const x = 1;\nserver.notATool(\"comms_nope\");\n"), []);
});

test("BOTH REGISTRATIONS SURVIVE AN ORDINARY BODY", () => {
  // The baseline the hard cases are compared against: with nothing unusual in the handler, the pair
  // is found. Without this, a fixture that never produced two registrations would make every test
  // below vacuous in the other direction.
  assert.deepEqual(acrossBody("const n = 1;"), ["comms_first", "comms_second"]);
});

test("A REGEX HOLDING BACKTICKS DOES NOT SWALLOW THE REGISTRATIONS AFTER IT", () => {
  // THE LIVE DEFECT, 2026-09-03. `channel-tools.mjs` fences message bodies with a replace whose
  // pattern is three backticks. Read as code, those backticks opened template literals; the third
  // one ran two lines on and ate the closing paren of the `.replace(`, so the enclosing
  // registration never closed and EVERY later registration in the file went with it -- three at
  // once, all silently ungoverned.
  assert.deepEqual(acrossBody("const fenced = body.replace(/```/g, \"'''\");"),
    ["comms_first", "comms_second"]);
});

test("a regex holding a quote does not either", () => {
  // The shape the old header PREDICTED while the live one was backticks. Cheap to cover now that
  // the construct is skipped rather than the character.
  assert.deepEqual(acrossBody('const s = t.replace(/"/g, "&quot;");'), ["comms_first", "comms_second"]);
});

test("a regex holding an unbalanced paren does not either", () => {
  // The one that corrupts the paren depth directly rather than by opening a bogus string.
  assert.deepEqual(acrossBody('const s = t.replace(/\\)/g, "");'), ["comms_first", "comms_second"]);
});

// NO TEST HERE FOR "A DIVISION IS NOT READ AS A REGEX", and the absence is a measurement rather
// than an oversight. One was written and then removed: with `startsARegex` forced to return true for
// every slash, not one registration was lost, on any body tried. Both readings SKIP the same
// characters -- a paren or quote between two slashes is passed over whether it is regex content or a
// string -- so over-eager detection does not move the paren depth, and `regexEnd`'s refusal to cross
// a newline bounds it to the line. The token gate is defence in depth for the character COUNT, not
// for finding registrations, and a test asserting otherwise would have been green for no reason.

test("A TEMPLATE LITERAL NESTED IN ANOTHER'S HOLE DOES NOT END THE OUTER ONE", () => {
  // THE THIRD DEFECT, found while fixing the second. `dashboard-tool.mjs` builds HTML with
  // `${rows.length ? `<table>…</table>` : "<p>None.</p>"}` five times over, and its templates carry
  // quotes (`<div class="stat">`). A backtick-to-backtick scan ends the outer template at the first
  // INNER backtick, which leaves the inner template's TEXT being read as code -- so a quote in it
  // opens a string that runs on. That file balanced anyway for a while because two later
  // misreadings cancelled, and correcting the regex handling changed which ones cancelled and lost
  // `comms_dashboard`. Luck is not a property worth preserving.
  assert.deepEqual(acrossBody('const html = `<div>${rows.length ? `<b>"${rows}</b>` : "none"}</div>`;'),
    ["comms_first", "comms_second"]);
});

test("a comment holding a quote does not either", () => {
  // THE FIRST DEFECT, kept because a fix with no test is a fix waiting to be undone: a code comment
  // containing a double quote made `comms_compact` vanish from the measurement.
  assert.deepEqual(acrossBody('// This comment has a " and a ) in it.'), ["comms_first", "comms_second"]);
});

test("a comment BETWEEN the paren and the slash does not decide whether it divides", () => {
  // `prev` has to survive a comment unchanged, and the comment has to sit in the one position where
  // that matters: directly before the slash. A first attempt put it a line earlier, where the token
  // before the regex is the `(` either way -- so the test passed against a parser that let comments
  // overwrite `prev`, which is the mutation it exists to catch. Here the comment is the last thing
  // before the pattern, so a parser that treats it as a token reads the regex as a division and its
  // backticks open a template that eats the registration.
  assert.deepEqual(acrossBody("const s = t.replace(\n        // which fence\n        /```/g, \"'''\");"),
    ["comms_first", "comms_second"]);
});

test("A REGISTRATION THAT NEVER CLOSES COSTS ONLY ITSELF", () => {
  // The parser used to `break` on an unbalanced span, which is how ONE misparse lost three tools.
  // Skipping forward instead bounds the damage to the registration that is actually broken -- and
  // the ratchet's count control still fires, because 2 written against 1 measured is a disagreement.
  const broken = '\n  server.tool(\n    "comms_broken",\n    "Unclosed.",\n';
  const found = registrationsIn(broken + source("comms_healthy")).map((t) => t.name);
  assert.ok(found.includes("comms_healthy"),
    `an unclosed registration took a later healthy one with it: ${JSON.stringify(found)}`);
});
