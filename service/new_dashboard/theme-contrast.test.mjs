// Primary-button text passes WCAG AA on every theme — measured on the value the BROWSER ends up with.
//
// WHY THIS FILE EXISTS RATHER THAN THE PYTHON ONE THAT CAME FIRST. My first attempt at this gate parsed
// `styles.css` and certified the eight `body[data-theme=...]` declarations. Those are a pre-JS
// FALLBACK. `applyTheme()` calls `derivePaletteVars()` and writes every variable as an INLINE body
// style, which beats any stylesheet rule — so the value audited was overwritten milliseconds into every
// boot, and the audit certified an artifact nobody sees.
//
// The runtime values were far worse than the one static failure I had "fixed". Measured on the real
// producer, five of eight themes were below AA, INCLUDING the default at 2.03:1:
//
//     default 2.03  forest 2.39  violet 2.04  crimson 4.07  indigo 2.21     (ember/ocean/graphite passed)
//
// THE CAUSE was `hexLuminance(hex) > 0.45 ? dark : light` — a BRIGHTNESS threshold standing in for a
// contrast one. Brightness is not contrast: #51c5b0 is bright enough to look light and nowhere near
// bright enough to carry white text. `contrastingForeground` now measures instead of guessing, and
// tries the house near-black/near-white first so pure black or white is reached only when a mid-tone
// accent needs it.
//
// IT MATTERS BEYOND THE EIGHT. Settings lets the operator set an arbitrary custom accent, and this
// function is the only thing between that colour and unreadable buttons — which is why the custom
// colours below are part of the gate rather than a footnote.

import assert from 'node:assert/strict';
import test from 'node:test';

import { LIGHTEST_SURFACE, MIN_CONTRAST, THEMES, applyTheme, contrastRatio, contrastingForeground, derivePaletteVars, readableAccentText } from './theme.js';

test('the ratio maths is right', () => {
  // POSITIVE CONTROL. Black on white is 21:1 by definition; a formula that cannot produce it cannot be
  // trusted to judge an accent, and every assertion below would be arithmetic on a broken function.
  assert.ok(Math.abs(contrastRatio('#000000', '#ffffff') - 21) < 0.1);
  assert.ok(Math.abs(contrastRatio('#ffffff', '#ffffff') - 1) < 0.01);
});

test('it would FAIL the exact pairs that prompted this', () => {
  // NEGATIVE CONTROL. The values the old threshold produced must read as failing — a gate that cannot
  // fail the case it was written for is decoration.
  assert.ok(contrastRatio('#f7fbff', '#51c5b0') < MIN_CONTRAST, 'the default theme at 2.03 reads as passing');
  assert.ok(contrastRatio('#f7fbff', '#d34b64') < MIN_CONTRAST, 'crimson at 4.07 reads as passing');
});

test('every SHIPPED theme passes AA on the value applyTheme actually writes', () => {
  const failures = {};
  for (const [name, palette] of Object.entries(THEMES)) {
    const vars = derivePaletteVars(palette);
    const ratio = contrastRatio(vars['--accent-contrast'], vars['--accent']);
    if (ratio < MIN_CONTRAST) failures[name] = Number(ratio.toFixed(2));
  }
  assert.deepEqual(failures, {},
    `themes below WCAG AA on their own primary buttons: ${JSON.stringify(failures)}. `
    + 'button.primary is 14px at weight 750 — bold, but not large enough for the 3:1 relaxation.');
  assert.ok(Object.keys(THEMES).length >= 8, 'the theme population shrank; this gate may be checking nothing');
});

test('an ARBITRARY operator accent gets a readable foreground', () => {
  // The path with no fixed population to enumerate. Settings accepts any hex, and a threshold that
  // guesses is exactly how the shipped themes ended up failing.
  // `>= MIN_CONTRAST || >= 4.4` stood here, which is just `>= 4.4` — a gate quietly holding a weaker
  // contract than the constant it imports. It was defensive and unnecessary: every one of these clears
  // 4.5 strictly.
  const awkward = ['#51c5b0', '#808080', '#767676', '#ffff00', '#000080', '#7f7f7f', '#c0c0c0',
                   '#ff00ff', '#8a8a8a', '#949494'];
  for (const accent of awkward) {
    const fg = contrastingForeground(accent);
    const ratio = contrastRatio(fg, accent);
    assert.ok(ratio >= MIN_CONTRAST,
      `accent ${accent} got ${fg} at ${ratio.toFixed(2)}:1 — below the ${MIN_CONTRAST} this file names`);
  }
});

test('it prefers the house colours and escalates only when it must', () => {
  // ANTI-VACUITY. Always returning pure black would satisfy every ratio assertion above while changing
  // the look of every theme; the point is to measure, not to maximise.
  assert.equal(contrastingForeground('#51c5b0'), '#06110f', 'a clearly-dark-text accent did not get the house near-black');
  assert.equal(contrastingForeground('#06110f'), '#f7fbff', 'a near-black accent did not get the house near-white');
});

test('applyTheme WRITES the derived foreground as an inline style, which is what beats the CSS', () => {
  // THE LINK THAT MAKES THE REST MATTER, and nothing asserted it. A correct producer nobody calls is a
  // shape this project has shipped before: six green tests on a pure helper whose call site was never
  // wired. `derivePaletteVars` being right is only useful because `applyTheme` puts its output on
  // `body.style`, where an inline value outranks every `body[data-theme=...]` rule in the stylesheet.
  //
  // Observing the WRITE rather than a rendered pixel is the honest bound here: it proves the value the
  // browser will resolve, without a headless render whose fidelity would itself need proving.
  const written = new Map();
  const body = { dataset: {}, style: { setProperty: (k, v) => written.set(k, v) } };
  const hadDoc = 'document' in globalThis;
  const prevDoc = globalThis.document;
  globalThis.document = {
    body,
    title: '',
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const hadStorage = 'localStorage' in globalThis;
  const prevStorage = globalThis.localStorage;
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  try {
    applyTheme({ dashboard_theme: 'crimson' }, { persist: false });
    assert.equal(body.dataset.theme, 'crimson', 'the theme key never reached the body');
    const fg = written.get('--accent-contrast');
    const bg = written.get('--accent');
    assert.ok(fg && bg, `applyTheme wrote no accent pair; it wrote: ${[...written.keys()].join(', ')}`);
    assert.equal(fg, derivePaletteVars(THEMES.crimson)['--accent-contrast'],
      'the value written differs from the one the producer derived');
    assert.ok(contrastRatio(fg, bg) >= MIN_CONTRAST,
      `the pair actually written is ${contrastRatio(fg, bg).toFixed(2)}:1`);
  } finally {
    if (hadDoc) globalThis.document = prevDoc; else delete globalThis.document;
    if (hadStorage) globalThis.localStorage = prevStorage; else delete globalThis.localStorage;
  }
});

test('the ESCALATION path is exercised, not merely present', () => {
  // #767676 sits at luminance 0.181, right in the overlap where both house colours fall short: near
  // black gives 4.47 and near white 4.44. Only pure black clears 4.5, at 4.62. Without a case in this
  // band the last two candidates are unreachable code that nothing proves works.
  const fg = contrastingForeground('#767676');
  assert.equal(fg, '#000000', 'a mid-tone accent did not escalate past the house colours');
  assert.ok(contrastRatio(fg, '#767676') >= MIN_CONTRAST);

  // ...and the house colours are still preferred either side of that band, so escalation is the
  // exception rather than the rule.
  assert.equal(contrastingForeground('#c0c0c0'), '#06110f');
  assert.equal(contrastingForeground('#000080'), '#f7fbff');
});

test('--accent-text is READABLE on the surfaces it is drawn on, for arbitrary accents too', () => {
  // THE SECOND TOKEN WITH THE SAME DEFECT, and I closed it wrongly the first time. It was
  // `hexLuminance(hex) > 0.38 ? hex : color-mix(in srgb, hex 64%, #ffffff)` — a brightness threshold
  // choosing the branch, then a FIXED 36% move toward white, which guarantees nothing.
  //
  // I argued that was structurally floored. That argument covered only the RAW branch; review showed
  // the mixed one has no floor. A valid operator accent of #000000 produced #5c5c5c — 2.65:1 on the
  // panel — and `--accent-text` colours `.chat-chip.active`, `.settings-tab.active`, the mine-message
  // heading and the action hovers. The eight shipped themes never hit it; Settings accepts any hex.
  for (const [name, palette] of Object.entries(THEMES)) {
    const vars = derivePaletteVars(palette);
    const ratio = contrastRatio(vars['--accent-text'], LIGHTEST_SURFACE);
    assert.ok(ratio >= MIN_CONTRAST, `theme ${name} renders accent text at ${ratio.toFixed(2)}:1`);
  }
});

test('the accents that BROKE it are the controls', () => {
  // NEGATIVE CONTROL with the exact values from the report, so a regression to a fixed mix fails here
  // rather than in an operator's Settings page.
  // THROUGH derivePaletteVars, NOT the helper. The first version of this called `readableAccentText`
  // directly, so restoring the old fixed-mix formula inside `derivePaletteVars` left every test green
  // -- the helper was correct and nothing proved it was CALLED. That is the same disconnected-call-site
  // shape as the applyTheme write, in the test written immediately after fixing it.
  for (const accent of ['#000000', '#0a0a0a', '#101010', '#1a1a1a', '#202020', '#2b2b2b', '#404040']) {
    const fg = derivePaletteVars({ accent, secondary: accent, tertiary: accent })['--accent-text'];
    const ratio = contrastRatio(fg, LIGHTEST_SURFACE);
    assert.ok(ratio >= MIN_CONTRAST, `accent ${accent} produced ${fg} at ${ratio.toFixed(2)}:1`);
  }
  // ...and the old formula must read as failing, or this control proves nothing.
  assert.ok(contrastRatio('#5c5c5c', '#15191b') < MIN_CONTRAST, 'the value that prompted this reads as passing');
});

test('it lightens only as far as it must', () => {
  // ANTI-VACUITY. Returning white always would satisfy every ratio assertion above while discarding
  // the accent the operator chose — the token exists to be a readable version of THEIR colour.
  const via = (accent) => derivePaletteVars({ accent, secondary: accent, tertiary: accent })['--accent-text'];
  assert.equal(via('#51c5b0'), '#51c5b0', 'an already-readable accent was altered');
  assert.notEqual(via('#000000'), '#ffffff', 'a dark accent was flattened to pure white');
});
