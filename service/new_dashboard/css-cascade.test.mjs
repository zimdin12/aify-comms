// Which declaration actually WINS, resolved rather than assumed.
//
// THREE RULES IN styles.css WERE DEAD, and all three failed the same way: a later rule with equal or
// higher specificity quietly beat the one that expressed the intent. A media query adds NO
// specificity, which is the fact every one of them tripped over.
//
//   * `#page-files.active { display:flex; overflow:hidden }` sat at the BOTTOM of the file, after the
//     `<=1100px` override that gives it `overflow:auto`. Equal specificity, so source order decided
//     and the Files list clipped on every narrow screen. Its sibling `#page-sessions.active` carries
//     the identical declaration and never clipped -- because it was written ABOVE the override. One
//     intent, written twice, in two places, with only one of them working.
//   * `.run-row { grid-template-columns: 1fr }` at `<=760px` was followed by an unbounded
//     `<=980px` rule, which also matches at 760px and came later. The mobile single-column Runs
//     layout never applied at all.
//   * `.chat-shell.compact` is (0,2,0) and the `<=760px` `.chat-shell` was (0,1,0), so a compact rail
//     kept its 200-252px column on a phone and the page scrolled sideways.
//
// WHY A RESOLVER AND NOT A GREP. Asserting that a line exists proves a line was written -- these bugs
// are entirely about which of several existing lines wins. So this parses the sheet, computes
// specificity, filters by whether each media block applies at a given width, and picks the winner the
// way a browser does: highest specificity, then last in source order. The assertions are about
// OUTCOMES at named viewport widths, which is the thing that was wrong.
//
// SCOPE, stated so a green run is not read as more: it resolves EXACT selector text within a
// comma-separated list. It does not do inheritance, shorthand expansion, `!important`, or matching a
// real DOM. That is enough for this class of bug and nothing beyond it.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHEET = fs.readFileSync(path.join(HERE, "styles.css"), "utf8");

/** Strip comments so a selector mentioned in prose is never parsed as a rule. */
function withoutComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * Flatten the sheet into rules, each carrying the media conditions it sits under and its position.
 *
 * Brace-walked rather than regexed, because a regex that finds `selector { ... }` cannot tell a rule
 * inside a media block from one outside it, and that distinction is the entire subject here.
 */
function parseRules(css) {
  const rules = [];
  const stack = [];
  let index = 0;
  let buffer = "";
  let order = 0;
  while (index < css.length) {
    const ch = css[index];
    if (ch === "{") {
      const head = buffer.trim();
      buffer = "";
      if (head.startsWith("@")) {
        stack.push(head);
      } else {
        // Find the matching close for this declaration block.
        let depth = 1;
        let body = "";
        index += 1;
        while (index < css.length && depth > 0) {
          if (css[index] === "{") depth += 1;
          else if (css[index] === "}") { depth -= 1; if (depth === 0) break; }
          body += css[index];
          index += 1;
        }
        rules.push({ selectors: head.split(",").map((s) => s.trim()).filter(Boolean),
                     body, media: [...stack], order: order++ });
      }
      index += 1;
      continue;
    }
    if (ch === "}") {
      if (stack.length) stack.pop();
      buffer = "";
      index += 1;
      continue;
    }
    buffer += ch;
    index += 1;
  }
  return rules;
}

/** (id, class, type) for a simple selector -- enough for the selectors this sheet uses. */
function specificity(selector) {
  const ids = (selector.match(/#[\w-]+/g) || []).length;
  const classes = (selector.match(/\.[\w-]+/g) || []).length
    + (selector.match(/\[[^\]]+\]/g) || []).length
    + (selector.match(/:(?!:)[\w-]+/g) || []).length;
  const types = (selector.replace(/[#.][\w-]+/g, " ").match(/\b[a-z][\w-]*\b/gi) || []).length;
  return [ids, classes, types];
}

function beats(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a.spec[i] !== b.spec[i]) return a.spec[i] > b.spec[i];
  }
  return a.order > b.order;
}

/** Does every media condition on this rule hold at `width`? Only width queries are understood. */
function appliesAt(media, width) {
  return media.every((query) => {
    if (!query.startsWith("@media")) return true;
    const maxes = [...query.matchAll(/max-width:\s*(\d+)px/g)].map((m) => Number(m[1]));
    const mins = [...query.matchAll(/min-width:\s*(\d+)px/g)].map((m) => Number(m[1]));
    return maxes.every((m) => width <= m) && mins.every((m) => width >= m);
  });
}

function declaration(body, property) {
  const found = [...body.matchAll(new RegExp(`(?:^|;)\\s*${property}\\s*:([^;]+)`, "g"))];
  return found.length ? found[found.length - 1][1].trim() : null;
}

const RULES = parseRules(withoutComments(SHEET));

/** The winning value of `property` for an exact `selector`, at a viewport `width`. */
function winner(selector, property, width) {
  let best = null;
  for (const rule of RULES) {
    if (!rule.selectors.includes(selector)) continue;
    if (!appliesAt(rule.media, width)) continue;
    const value = declaration(rule.body, property);
    if (value === null) continue;
    const candidate = { value, spec: specificity(selector), order: rule.order };
    if (!best || beats(candidate, best)) best = candidate;
  }
  return best ? best.value : null;
}

// -- controls on the instrument ------------------------------------------------------------------

test("the parser finds a real sheet", () => {
  assert.ok(RULES.length > 300, `only ${RULES.length} rules parsed -- the parser is broken, not the sheet`);
  assert.ok(RULES.some((r) => r.media.length > 0), "no rule was seen inside a media block");
  assert.ok(RULES.some((r) => r.media.length === 0), "every rule was seen inside a media block");
});

test("a selector that does not exist resolves to nothing", () => {
  // NEGATIVE CONTROL. A resolver that answered for anything would pass every assertion below.
  assert.equal(winner(".no-such-selector-here", "display", 700), null);
});

test("comments are not parsed as rules", () => {
  // The fixes added comments that NAME the selectors they are about. A parser that read those would
  // resolve against its own documentation -- which has already happened once in this repo.
  const rules = parseRules(withoutComments("/* #page-files.active { overflow: hidden; } */\n.a { color: red; }"));
  assert.equal(rules.length, 1);
  assert.deepEqual(rules[0].selectors, [".a"]);
});

test("specificity is computed, not guessed", () => {
  assert.deepEqual(specificity("#page-files.active"), [1, 1, 0]);
  assert.deepEqual(specificity(".chat-shell.compact"), [0, 2, 0]);
  assert.deepEqual(specificity(".chat-shell"), [0, 1, 0]);
  assert.ok(beats({ spec: [0, 2, 0], order: 0 }, { spec: [0, 1, 0], order: 99 }),
    "a more specific rule must beat a later one -- this is the fact all three bugs tripped over");
});

// -- the three outcomes ---------------------------------------------------------------------------

test("the Files page scrolls on a narrow screen instead of clipping", () => {
  assert.equal(winner("#page-files.active", "overflow", 700), "auto",
    "the Files list is back to `overflow: hidden` below 1100px -- the desktop rule has moved after "
    + "the narrow override again, and the list clips with no way to scroll");
});

test("Sessions behaves the same way, which is the control that made Files look fine", () => {
  // POSITIVE CONTROL from the live sheet: this sibling always worked, and its working is exactly why
  // the Files bug went unnoticed. If this ever fails, the override itself is gone.
  assert.equal(winner("#page-sessions.active", "overflow", 700), "auto");
});

test("both pages still fill the viewport on a desktop", () => {
  // CONTRADICTION ARM: "auto everywhere" would satisfy the two tests above and destroy the layout.
  assert.equal(winner("#page-files.active", "overflow", 1400), "hidden");
  assert.equal(winner("#page-sessions.active", "overflow", 1400), "hidden");
});

test("the Runs list is one column on a phone", () => {
  assert.equal(winner(".run-row", "grid-template-columns", 700), "1fr",
    "the tablet rule reaches phone widths again, so the single-column mobile layout is dead");
});

test("and still three columns on a tablet", () => {
  // CONTRADICTION ARM: bounding the tablet query too far would make this 1fr and nobody would notice.
  assert.equal(winner(".run-row", "grid-template-columns", 900), "22px minmax(0, 1fr) auto");
});

test("a compact chat rail collapses on a phone rather than scrolling sideways", () => {
  assert.equal(winner(".chat-shell.compact", "grid-template-columns", 700), "1fr",
    "compact chat keeps a fixed rail column on a phone -- the mobile rule no longer names the "
    + "compact class, and a media query adds no specificity to beat it with");
});

test("compact chat keeps its narrow rail on a desktop", () => {
  // CONTRADICTION ARM: collapsing it everywhere would pass the test above and delete the feature.
  assert.equal(winner(".chat-shell.compact", "grid-template-columns", 1400),
    "minmax(200px, 252px) minmax(0, 1fr)");
});
