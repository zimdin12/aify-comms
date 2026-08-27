// "Is this a usable hex colour" has ONE answer, and it lowercases.
//
// THE SHAPE. `normalizedHexColor` is exported from theme.js and is the single source of truth. Two
// other modules hand-rolled the same `/^#[0-9a-fA-F]{6}$/` instead of calling it -- three
// implementations of one question, which agree right up until somebody widens one of them to accept
// 3-digit hex or an alpha channel. The same shape produced a real defect elsewhere in this repo the
// same day: `scripts/installed-endpoint.sh` exists because two copies of one endpoint regex both
// silently stopped matching, and the launcher-delegation parse had THREE copies.
//
// AND THEY HAD ALREADY DIVERGED. `theme.js` lowercases a valid colour; `settings-fields.mjs` returned
// it exactly as stored. `<input type="color">` normalises its own value to lowercase, so a setting
// saved as `#AABBCC` rendered `#AABBCC` in the code label beside a swatch driven by `#aabbcc`, with
// the theme applying the lowercase one. Not a crash -- a surface showing two spellings of one colour.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { settingsFieldHtml } from './settings-fields.mjs';
import { normalizedHexColor } from './theme.js';

const HERE = dirname(fileURLToPath(import.meta.url));

test('the helper is the vocabulary: valid in, lowercase out', () => {
  assert.equal(normalizedHexColor('#AABBCC', 'fb'), '#aabbcc');
  assert.equal(normalizedHexColor('#aabbcc', 'fb'), '#aabbcc');
  assert.equal(normalizedHexColor('  #E5484D  ', 'fb'), '#e5484d', 'surrounding whitespace was not trimmed');
});

test('everything that is not a 6-digit hex falls back', () => {
  // The fallback is what the caller shows instead, so a value slipping through as "valid" would put
  // an unusable string into a style attribute.
  for (const bad of ['', '   ', null, undefined, '#abc', '#aabbccdd', 'aabbcc', '#gghhii', 'red',
                     'rgb(1,2,3)', '#aabbc', 0, [], {}]) {
    assert.equal(normalizedHexColor(bad, 'FALLBACK'), 'FALLBACK', `accepted ${JSON.stringify(bad)}`);
  }
});

test('the fallback is returned verbatim, not normalised', () => {
  // Callers pass a preset that is already correct; re-normalising it would be a second opinion about
  // a value that was never in question.
  assert.equal(normalizedHexColor('nope', '#51C5B0'), '#51C5B0');
});

test('ONE implementation: the raw regex lives nowhere but the helper', () => {
  // The gate. A fourth copy fails here rather than being noticed when two surfaces disagree.
  const PATTERN = '[0-9a-fA-F]{6}';
  const files = readdirSync(HERE).filter((f) => /\.(mjs|js)$/.test(f) && !f.includes('.test.'));
  assert.ok(files.length > 30, `positive control: only ${files.length} dashboard modules found`);

  const carriers = files.filter((f) => readFileSync(join(HERE, f), 'utf8').includes(PATTERN));
  assert.deepEqual(
    carriers, ['theme.js'],
    `the hex-colour regex must live only in theme.js, beside normalizedHexColor. Found in: ${carriers.join(', ')}`,
  );
});

test('CALL SITE: the rendered colour field is normalised, not merely passed through', () => {
  // BEHAVIOURAL, because the source-regex version of this was fooled. It asserted the module
  // MENTIONS `normalizedHexColor` -- which the import line does, even with every call removed. A
  // mutation replacing the call with `value || fallback` survived it. Rendering the field asks what
  // the user actually sees.
  const item = { key: 'dashboard_accent_color', type: 'color', label: 'Accent' };

  const upper = settingsFieldHtml(item, '#AABBCC', {});
  assert.match(upper, /#aabbcc/, 'a stored #AABBCC was rendered without being normalised');
  assert.doesNotMatch(upper, /#AABBCC/,
    'the field shows #AABBCC beside a swatch the browser drives with #aabbcc -- two spellings of ' +
    'one colour on one row, which is the inconsistency this consolidation removes');

  // And an unusable value still falls back rather than reaching a style attribute.
  const bad = settingsFieldHtml(item, 'not-a-colour', {});
  assert.doesNotMatch(bad, /not-a-colour/, 'an invalid colour reached the rendered field');
  assert.match(bad, /value="#[0-9a-f]{6}"/, 'the fallback was not a usable hex colour');
});

test('terminalAccentColor asks the helper rather than its own regex', () => {
  // Its call site needs the DOM, so this checks the CALL is present -- `normalizedHexColor(` with the
  // paren, which the import line does not contain. Weaker than the behavioural test above, and
  // labelled as such rather than dressed up.
  const src = readFileSync(join(HERE, 'settings-panel.mjs'), 'utf8');
  assert.match(src, /normalizedHexColor\(/, 'settings-panel.mjs imports the helper but never calls it');
});
