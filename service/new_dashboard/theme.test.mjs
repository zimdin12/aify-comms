import { test } from 'node:test';
import assert from 'node:assert/strict';
import { THEMES, themeKey, normalizedHexColor, hexLuminance, paletteFromSettings, derivePaletteVars } from './theme.js';

test('8 named themes exist incl. default', () => {
  assert.equal(Object.keys(THEMES).length, 8);
  for (const k of ['default', 'forest', 'violet', 'ember', 'ocean', 'graphite', 'crimson', 'indigo']) {
    assert.ok(THEMES[k] && THEMES[k].accent, `${k} has an accent`);
  }
});

test('themeKey falls back to default for unknown', () => {
  assert.equal(themeKey('ocean'), 'ocean');
  assert.equal(themeKey('nope'), 'default');
  assert.equal(themeKey(''), 'default');
  assert.equal(themeKey(undefined), 'default');
});

test('normalizedHexColor validates 6-digit hex', () => {
  assert.equal(normalizedHexColor('#AABBCC', '#000000'), '#aabbcc');
  assert.equal(normalizedHexColor('red', '#123456'), '#123456');
  assert.equal(normalizedHexColor('#abc', '#123456'), '#123456');
});

test('paletteFromSettings: explicit color wins, else preset', () => {
  const p1 = paletteFromSettings({}, 'ocean');
  assert.equal(p1.accent, THEMES.ocean.accent);
  const p2 = paletteFromSettings({ dashboard_primary_color: '#ff0000' }, 'ocean');
  assert.equal(p2.accent, '#ff0000');
  // local cache used when no explicit setting
  const p3 = paletteFromSettings({}, 'default', { dashboard_secondary_color: '#00ff00' });
  assert.equal(p3.secondary, '#00ff00');
});

test('derivePaletteVars produces the accent var set', () => {
  const vars = derivePaletteVars({ accent: '#51c5b0', secondary: '#74b7ff', tertiary: '#dfb156' });
  assert.equal(vars['--accent'], '#51c5b0');
  assert.ok(vars['--accent-strong'].includes('color-mix'));
  assert.ok(vars['--secondary']);
  // `--secondary-contrast` and `--tertiary-contrast` are GONE, producer and all. They were emitted
  // into the inline style map on every apply and read by no CSS rule anywhere -- a variable nothing
  // consumes is not a contract, and asserting its existence was the only thing keeping it alive.
  assert.equal(vars['--tertiary-contrast'], undefined);
  assert.equal(vars['--secondary-contrast'], undefined);
});

test('hexLuminance: white brighter than black', () => {
  assert.ok(hexLuminance('#ffffff') > hexLuminance('#000000'));
});
