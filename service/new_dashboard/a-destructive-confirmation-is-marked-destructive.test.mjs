// A confirmation that guards a DELETE wears the red button.
//
// `openDialog` paints the confirm button red only when the caller passes `tone: 'danger'`, and
// every one of the twenty-one call sites types that opt-in by hand. On 2026-08-29 two wore it:
// "Delete shared file" and "Remove X from #chan ... history remains" -- the second of which is
// reversible by rejoining. Meanwhile "Remove agent X? This tombstones the identity", "Delete
// channel #X ... for everyone" and "Unsend this message" were styled exactly like "Close this run
// as operator-reviewed". The cue existed, and it ranked the wrong controls.
//
// The rule is DERIVED from the code beside the call rather than from its English: a confirmation
// whose function goes on to issue a `method: 'DELETE'` is destroying a row, and a keyword scan of
// the message cannot know that -- it reads "It does not interrupt anything" as an interrupt.
//
// Stop/interrupt confirmations are red too, and nothing here derives them: they POST to a control
// endpoint that looks like any other POST. Those were marked by hand and this gate does not defend
// them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { declarationSpan } from './extraction-proof.mjs';

const DIR = dirname(fileURLToPath(import.meta.url));
const FN_HEAD = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function(?:\s+|\s*\*\s*)([A-Za-z_$][\w$]*)\s*\(/;

/**
 * Every `uiConfirm` in one source, with the facts that decide whether it should be red.
 *
 * The enclosing function comes from the repo's own parser. Four hand-rolled JS parsers gave four
 * wrong answers on 2026-08-14, one of them costing a written and proven slice.
 */
export function confirmationSites(source, where = '<memory>') {
  const lines = source.split('\n');
  const spans = [];
  for (const line of lines) {
    const m = FN_HEAD.exec(line);
    if (!m) continue;
    const span = declarationSpan(source, m[1]);
    if (span) spans.push({ fn: m[1], ...span });
  }
  const sites = [];
  lines.forEach((line, i) => {
    if (!line.includes('uiConfirm(')) return;
    const owner = spans.find((s) => i >= s.start && i <= s.end);
    sites.push({
      where: `${where}:${i + 1}`,
      fn: owner ? owner.fn : null,
      // What this confirmation goes on to do, and how it is dressed. The opts trail the message,
      // which may itself run over several lines, so read to the end of the call's own statement.
      guardsDelete: owner ? /method:\s*'DELETE'/.test(lines.slice(i, owner.end + 1).join('\n')) : false,
      marked: /danger/.test(lines.slice(i, i + 4).join('\n')),
    });
  });
  return sites;
}

function dashboardSites() {
  const files = readdirSync(DIR).filter((n) => (n.endsWith('.mjs') || n.endsWith('.js'))
    && !n.includes('.test.') && n !== 'ui.js' && n !== 'extraction-proof.mjs'
    && !n.startsWith('app.before-'));
  const out = [];
  for (const name of files) {
    const src = readFileSync(join(DIR, name), 'utf8');
    if (!src.includes('uiConfirm(')) continue;
    out.push(...confirmationSites(src, name));
  }
  return out;
}

test('a confirmation that guards a DELETE wears the red button', () => {
  const plain = dashboardSites().filter((s) => s.guardsDelete && !s.marked);
  assert.deepEqual(plain.map((s) => `${s.where} ${s.fn}`), [],
    'these confirmations delete a row and look like any other prompt; pass { tone: \'danger\' }');
});

test('the scan finds the dashboard\'s confirmations and resolves every one to a function', () => {
  const sites = dashboardSites();
  // A scan that found nothing would pass the test above by looking at nothing.
  assert.ok(sites.length >= 15, `expected the dashboard's confirmations, found ${sites.length}`);
  assert.ok(sites.some((s) => s.guardsDelete), 'no site guards a DELETE — the DELETE probe is dead');
  assert.ok(sites.some((s) => s.marked), 'no site is marked — the tone probe is dead');
  assert.deepEqual(sites.filter((s) => !s.fn).map((s) => s.where), [],
    'the parser found no enclosing function for these, so their region was never read');
});

test('a DELETE-guarding confirmation with no tone is reported', () => {
  const planted = [
    'export async function removeTheThing(id) {',
    "  if (!await uiConfirm(`Remove ${id}?`)) return;",
    "  await api(`/things/${id}`, { method: 'DELETE' });",
    '}',
  ].join('\n');
  const [site] = confirmationSites(planted, 'planted.mjs');
  assert.equal(site.fn, 'removeTheThing');
  assert.equal(site.guardsDelete, true);
  assert.equal(site.marked, false);
});

test('the same confirmation with the tone is not reported', () => {
  const planted = [
    'export async function removeTheThing(id) {',
    "  if (!await uiConfirm(`Remove ${id}?`, { tone: 'danger' })) return;",
    "  await api(`/things/${id}`, { method: 'DELETE' });",
    '}',
  ].join('\n');
  const [site] = confirmationSites(planted, 'planted.mjs');
  assert.equal(site.guardsDelete, true);
  assert.equal(site.marked, true);
});

test('the tone reaches a rule that actually paints something', () => {
  // The producer and the consumer of the cue, checked against each other: ui.js puts `danger` on
  // the confirm button, and exactly one CSS rule reads that pair. A tone nothing styles is
  // decoration, and every call site above would be paying for it.
  const ui = readFileSync(join(DIR, 'ui.js'), 'utf8');
  assert.match(ui, /dialog-confirm\$\{tone === 'danger' \? ' danger' : ''\}/,
    'ui.js no longer puts the danger class on the confirm button');
  const css = readFileSync(join(DIR, 'styles.css'), 'utf8');
  assert.match(css, /\.dialog-confirm\.danger\s*\{/,
    'nothing styles .dialog-confirm.danger, so tone: danger renders identically to no tone');
});
