// Every agent status reads one colour on every surface: the dashboard's dot, its messenger filter chip,
// and aify-env's terminal.
//
// THE OPERATOR, 2026-09-28: "are all statuses in sync, like same colors everywhere? otherwise it is
// confusing". The dashboard's colours live in styles.css and aify-env's in its own table
// (lib/agent-status-palette.mjs), two repos with nothing binding them. This reads both and compares
// HUES, since a terminal has eight colours and the stylesheet has hex: amber and yellow are one hue.
// It also found the messenger filter with no `shell` chip, so filtering hid every agent in that state.
//
// Herdr is not held here: its sidebar has its own fixed palette and states (working, idle, blocked,
// done), which aify cannot set.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { siblingCheckout } from "../../mcp/stdio/tests/_sibling-checkout.mjs";
import { AGENT_STATUSES, AGENT_STATUS_MEANINGS, STATUS_KINDS, statusChipsHtml, statusLegendHtml } from "./status.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Comments removed, or the one above a rule reads as part of its selector.
const CSS = fs.readFileSync(path.join(HERE, "styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const HTML = fs.readFileSync(path.join(HERE, "index.html"), "utf8");

/** `--name: value` from the first :root block. */
const ROOT_VARS = Object.fromEntries([...(/:root\s*\{([^}]*)\}/.exec(CSS)?.[1] ?? "").matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)]
  .map(([, name, value]) => [name, value.trim()]));

/** The first colour a rule for exactly `selector` paints, as #rrggbb, or null. `transparent` is skipped. */
function colourOf(selector) {
  for (const [, selectors, body] of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!selectors.split(",").map((s) => s.trim()).includes(selector)) continue;
    const token = /var\((--[\w-]+)(?:\s*,\s*(#[0-9a-f]{3,6}))?\)|#[0-9a-f]{6}\b/i.exec(body);
    if (!token) continue;
    if (!token[1]) return token[0].toLowerCase();
    const value = ROOT_VARS[token[1]] ?? token[2];
    return /^#[0-9a-f]{6}$/i.test(value ?? "") ? value.toLowerCase() : null;
  }
  return null;
}

/** The terminal colour name a hex colour is closest to by hue: what "the same colour" means across the two. */
function hueName(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const light = (max + min) / 2;
  const saturation = max === min ? 0 : (max - min) / (1 - Math.abs(2 * light - 1));
  if (saturation < 0.2) return "grey";
  const d = max - min;
  const h = (max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4) * 60;
  const deg = (h + 360) % 360;
  if (deg < 20 || deg >= 330) return "red";
  if (deg < 70) return "yellow";
  if (deg < 165) return "green";
  if (deg < 200) return "cyan";
  if (deg < 255) return "blue";
  return "magenta";
}

const dotHue = (status) => {
  const colour = colourOf(`.status-dot.${STATUS_KINDS[status]?.dotKind}`);
  assert.ok(colour, `the dashboard paints no colour for ${status} (dot ${STATUS_KINDS[status]?.dotKind})`);
  return hueName(colour);
};

test("CONTROL: the hue reader names the dashboard's colours, and can tell them apart", () => {
  assert.equal(hueName("#3fc6d6"), "cyan");
  assert.equal(hueName("#dfb156"), "yellow");
  assert.equal(hueName("#88c999"), "green");
  assert.equal(hueName("#6fb4ff"), "blue");
  assert.equal(hueName("#ee6a74"), "red");
  assert.equal(hueName("#9eaaa5"), "grey");
  assert.notEqual(hueName("#3fc6d6"), hueName("#6fb4ff"), "shell and available must not read as one hue");
});

test("aify-env's terminal paints every agent status in the dashboard's hue for it", async () => {
  const env = siblingCheckout("aify-env", path.join("lib", "agent-status-palette.mjs"));
  assert.ok(env.dir, `no aify-env checkout with the palette; looked in ${env.looked.join(", ")} (set ${env.variable})`);
  const { AGENT_STATUS_HUES } = await import(pathToFileURL(path.join(env.dir, "lib", "agent-status-palette.mjs")).href);
  const differ = AGENT_STATUSES
    .map((status) => ({ status, dashboard: dotHue(status), terminal: AGENT_STATUS_HUES[status] }))
    .filter((row) => row.dashboard !== row.terminal);
  assert.deepEqual(differ, [], "a status reads one colour on the dashboard and another in aify-env");
});

test("every agent status has a messenger filter chip, in its own dot colour (v0.7.7)", () => {
  // The chips were hand markup missing `starting` and `misconfigured`, so a newly spawned agent
  // vanished under any chip filter. They are generated from AGENT_STATUSES now.
  const chips = [...statusChipsHtml().matchAll(/class="chat-chip status dot s-([\w-]+)" data-chat-status="([\w-]+)"/g)];
  assert.deepEqual(chips.map(([, , status]) => status), AGENT_STATUSES, "a status has no chip");
  for (const [, dotClass, status] of chips) {
    const colour = colourOf(`.chat-chip.status.dot.s-${dotClass}::before`);
    assert.ok(colour, `the ${status} chip paints no colour of its own, so it shows the grey default`);
    assert.equal(hueName(colour), dotHue(status), `the ${status} chip is not the ${status} dot's colour`);
  }
  assert.doesNotMatch(HTML, /data-chat-status="/, "index.html still carries a hand-written chip");
});

test("the Help legend lists every agent status, and says how many there are", () => {
  const legend = statusLegendHtml();
  for (const status of AGENT_STATUSES) {
    assert.match(legend, new RegExp(`<strong>${STATUS_KINDS[status].label}</strong>`), `${status} is not in the legend`);
  }
  assert.deepEqual(Object.keys(AGENT_STATUS_MEANINGS), AGENT_STATUSES, "a status has no meaning, or a meaning no status");
  assert.doesNotMatch(HTML, /six agent states/, "the legend still says six");
  assert.match(HTML, /id="status-legend"/, "CONTROL: the legend's host is in the page");
});

test("the Working now tile is the working dot's colour", () => {
  const tile = colourOf('.metric[data-tone="working"]');
  assert.ok(tile, "the working tile paints no colour");
  assert.equal(hueName(tile), dotHue("working"));
});
