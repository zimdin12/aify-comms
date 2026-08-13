// The bridge's name-validation boundary, tested directly for the first time.
//
// `validateName` gates twelve tools and decides whether a caller-supplied string is allowed to become a
// URL path segment, a shared-artifact filename, or an agent registry key. It lived in `server.js`, the
// bin entry point, which nothing imports — so the check every one of those tools relies on had no test.
// Extracting it (v0.5.4 layer 0) is what makes these assertions possible.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SAFE_NAME_RE, validateName } from "../safe-name.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("ordinary names this project actually uses are accepted", () => {
  // The failure opposite to the interesting one: a validator so strict it rejects real agent ids. Every
  // name here is one the repo or its examples genuinely register.
  for (const name of [
    "claude-main", "comms-senior-dev", "mc-senior-dev", "agent_1", "Agent.2",
    "a", "team-manager", "coder-01", "x".repeat(128),
  ]) {
    assert.doesNotThrow(() => validateName(name), `${name} is a legitimate name and must be accepted`);
  }
});

test("nothing that could escape a directory or split a path is accepted", () => {
  // The reason the function exists. These strings reach filesystem joins and URL path segments.
  for (const name of [
    "..", ".", "../etc/passwd", "..\\windows", "a/b", "a\\b", "/abs", "C:/x",
    ".hidden", "a b", "a\tb", "a\nb", "a%2fb", "a?b=1", "a#frag", "a:b", "a;b",
    "a|b", "a*b", "a\0b", "<script>", "a'b", 'a"b',
  ]) {
    assert.throws(() => validateName(name), /Invalid name/, `${JSON.stringify(name)} must be rejected`);
  }
});

test("the leading character carries the traversal guard", () => {
  // A dot is legal INSIDE a name and illegal as its first character, and that asymmetry is the whole
  // defence against `..`. Both halves asserted, because relaxing the first position would be an easy
  // and invisible mistake.
  assert.doesNotThrow(() => validateName("a.b"), "a dot inside a name is fine");
  assert.doesNotThrow(() => validateName("a..b"), "even a double dot inside a name — no separator, no escape");
  assert.throws(() => validateName(".ab"), /Invalid/, "a leading dot must be rejected");
  assert.throws(() => validateName("-ab"), /Invalid/, "a leading hyphen must be rejected");
  assert.throws(() => validateName("_ab"), /Invalid/, "a leading underscore must be rejected");
});

test("the length ceiling is 128 characters, enforced at the boundary", () => {
  assert.doesNotThrow(() => validateName("x".repeat(128)), "128 is the documented maximum and must pass");
  assert.throws(() => validateName("x".repeat(129)), /Invalid/, "129 must fail");
  assert.throws(() => validateName(""), /Invalid/, "an empty name must fail");
});

test("the error names the field, so a caller learns WHICH argument was wrong", () => {
  // Twelve tools share this function and pass different labels. An error that always said "name" would
  // send an operator looking at the wrong parameter.
  assert.throws(() => validateName("../x", "channel"), /Invalid channel:/);
  assert.throws(() => validateName("../x", "filename"), /Invalid filename:/);
  assert.throws(() => validateName("../x"), /Invalid name:/, "the label defaults to 'name'");
  // And it quotes the offending value back, which is what makes a rejected name debuggable.
  assert.throws(() => validateName("bad name"), /Got: "bad name"/);
});

test("KNOWN GAP, pinned not fixed: null and undefined are ACCEPTED", () => {
  // `RegExp.test` coerces its argument, so `null` becomes the string "null" and `undefined` becomes
  // "undefined" — both of which match the pattern perfectly. A caller that passes a missing value gets
  // no error and proceeds with the literal word as a name.
  //
  // Not exploitable as traversal: the coerced forms contain no separator. But it means a bug upstream
  // surfaces as an agent or file called "undefined" instead of a rejected call, and this function is
  // the layer that was supposed to catch it.
  //
  // This is a structural slice and changing it is a behavioural fix, so the current behaviour is
  // recorded here rather than corrected. Change these assertions only on purpose.
  assert.doesNotThrow(() => validateName(null), "current behaviour: null coerces to \"null\" and passes");
  assert.doesNotThrow(() => validateName(undefined), "current behaviour: undefined coerces and passes");
  assert.doesNotThrow(() => validateName(123), "current behaviour: a number coerces and passes");
  // Objects and arrays do NOT slip through, because their coerced forms contain illegal characters.
  assert.throws(() => validateName({}), /Invalid/, "[object Object] contains a space and brackets");
  assert.throws(() => validateName(["a", "b"]), /Invalid/, "a,b contains a comma");
});

test("the regex is anchored at both ends", () => {
  // An unanchored pattern would accept "../../etc/passwd" on the strength of the "etc" inside it. This
  // asserts the property directly rather than trusting the cases above to have covered it.
  assert.ok(SAFE_NAME_RE.source.startsWith("^"), "must be anchored at the start");
  assert.ok(SAFE_NAME_RE.source.endsWith("$"), "must be anchored at the end");
  assert.ok(!SAFE_NAME_RE.global, "a global regex carries lastIndex between calls and would alternate results");
});

test("server.js no longer declares either — exactly one owner", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.doesNotMatch(src, /^(?:const|let|var)\s+SAFE_NAME_RE\b/m, "SAFE_NAME_RE must not be redeclared");
  assert.doesNotMatch(src, /^(?:export\s+)?function\s+validateName\b/m, "validateName must be imported");
  assert.match(src, /(?<![\w.])validateName\(/, "server.js is still expected to CALL it");
});
