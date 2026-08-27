// The fleet-pulse panel does not use a STATUS name for the set that contains it.
//
// READ OFF THE LIVE DASHBOARD. The panel read "ONLINE AGENTS — 27 online" above 27 rows of which 6
// carried an `online` chip, 20 carried `available` and 1 carried `working`. One word doing two jobs
// on one screen, in a UI whose entire status apparatus exists to keep those apart: `online` is a
// declared member of AGENT_STATUSES, with its own chip and its own meaning in the contract ("Live
// worker, between turns"), and it was also the heading over its own siblings.
//
// The umbrella already had a name. `status.js` declares LIVE_AGENT_STATUSES and
// NON_LIVE_AGENT_STATUSES, and the sidebar's connection indicator has always read "live".
//
// THE API FIELD IS UNCHANGED. `onlineAgents` is what the service emits; renaming an emitted field is
// a contract change and this is a label fix. The panel reads that field and prints the right word.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { fleetPulseHtml } from './analytics.js';
import { AGENT_STATUSES, NON_LIVE_AGENT_STATUSES } from './status.js';

const HERE = dirname(fileURLToPath(import.meta.url));

const PULSE = {
  onlineAgents: 27,
  workingNow: 1,
  fleetWorkingMinutes: 0,
  fleetUtilizationPct: 0,
  messages: { count: 0, perHour: 0 },
  openReplyContracts: 0,
  overdueReplyContracts: 0,
  agents: [],
};

/** Only the text a reader sees: element bodies, not attributes or tooltips. */
function visibleText(html) {
  return (html.match(/>[^<>]+</g) || []).map((m) => m.slice(1, -1).trim()).filter(Boolean).join(' | ');
}

test('the count and its heading both say LIVE', () => {
  const text = visibleText(fleetPulseHtml(PULSE, 60));
  assert.match(text, /27 live/);
  assert.match(text, /Live agents/);
});

test('no VISIBLE label calls the umbrella "online"', () => {
  // The assertion that would have failed before. Scoped to visible text on purpose: `onlineAgents`
  // still appears in the source as the field name, and must, so a source-wide ban would be wrong.
  const text = visibleText(fleetPulseHtml(PULSE, 60));
  assert.doesNotMatch(
    text, /\bonline\b/i,
    `a visible label still uses a status name for the set containing it: ${text}`,
  );
});

test('the empty state says it too', () => {
  const text = visibleText(fleetPulseHtml({ ...PULSE, onlineAgents: 0, workingNow: 0 }, 60));
  assert.match(text, /No live agents right now/);
  assert.doesNotMatch(text, /\bonline\b/i);
});

/** The board heading only. Scoped because the same count also appears on the Working-now card, and
 *  a whole-page match let a mutation that DELETED the heading's count pass. */
function boardHead(html) {
  const m = /<div class="pulse-board-head">([\s\S]*?)<\/div>/.exec(html);
  assert.ok(m, 'positive control: the pulse board head was not found at all');
  return visibleText(m[1]);
}

test('THE FIELD IS STILL READ IN THE HEADING, so this is a label change and not a deletion', () => {
  // ANTI-VACUITY, and it needed a second pass. The first version matched anywhere on the page, so
  // deleting the heading's count still passed -- the Working-now card carries the same number.
  for (const n of [0, 1, 27, 143]) {
    const head = boardHead(fleetPulseHtml({ ...PULSE, onlineAgents: n }, 60));
    assert.ok(head.includes(`${n} live`), `the heading lost its count: ${head}`);
  }
});

test('the Working-now card carries the count too, and says live', () => {
  const text = visibleText(fleetPulseHtml({ ...PULSE, onlineAgents: 9 }, 60));
  assert.match(text, /9 live/);
});

test('"live" is the codebase\'s OWN word for this set, not one invented here', () => {
  // The reason this word and not "active" or "up". If the declarations that justify it are renamed,
  // this fails and somebody re-picks the label deliberately.
  const src = readFileSync(join(HERE, 'status.js'), 'utf8');
  assert.match(src, /export const LIVE_AGENT_STATUSES/);
  assert.match(src, /export const NON_LIVE_AGENT_STATUSES/);
});

test('the umbrella genuinely CONTAINS the status it used to be named after', () => {
  // The whole defect in one assertion: `online` is a member of the set, so it cannot also be its
  // name without the panel contradicting the chips in its own rows.
  assert.ok(AGENT_STATUSES.includes('online'));
  assert.ok(!NON_LIVE_AGENT_STATUSES.includes('online'), 'online is a LIVE status, i.e. inside the umbrella');
  assert.ok(
    AGENT_STATUSES.filter((s) => !NON_LIVE_AGENT_STATUSES.includes(s)).length > 1,
    'the umbrella holds more than one status, or naming it after one would be harmless',
  );
});

test('the tooltip says what live MEANS, and names the same exclusions the service uses', () => {
  // The words and the arithmetic have to agree: `status_engine.NON_LIVE_AGENT_STATUSES` is what the
  // analytics board now classifies with, and the tooltip explains the number that comes out of it.
  const html = fleetPulseHtml(PULSE, 60);
  for (const status of NON_LIVE_AGENT_STATUSES) {
    assert.ok(html.includes(status), `the tooltip does not name ${status} as excluded`);
  }
});
