// Each out-of-band loader reports its own failure, because none of them lets one escape.
//
// THE DEFECT THIS REPLACES WAS MINE, and it was the same shape as the bug it was written to fix.
// 85780f7a added `noteSliceFailure(...)` to the poll cycle's four catches, so a fetch outside the
// allSettled array would stop failing silently. Three of those four catches can never run:
//
//     loadFiles              catch (_) { /* keep prior */ }        -- swallows, returns normally
//     chatLoadChannels       catch (_) { /* keep prior list */ }   -- swallows
//     loadContractsForState  catch (err) { toast(...) }            -- swallows
//     chatLoadConversation   propagates                            -- the only live one
//
// `await loadFiles()` cannot throw, so the caller has nothing to catch and the report never fires.
// A handler wired to an event that does not occur: exactly the interrupt attribution, exactly the
// helper-tested-instead-of-the-call-site rule, committed by the person who wrote both of them down.
//
// The report now lives INSIDE each loader's own catch, where the failure is actually observed. That
// also makes it fire when a loader fails during a user ACTION -- shared-files calls loadFiles three
// times outside the poll, message-actions calls chatLoadChannels four times -- which the call-site
// version could never have covered. Re-throwing instead was not an option: several of those callers
// do not catch at all, and an unhandled rejection in an event handler is a worse bug.
//
// The poll cycle keeps its catches. They are defence in depth now rather than dead: they still cover
// chatLoadConversation, and any loader that stops swallowing later.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { OUT_OF_BAND_SLICES } from './refresh-status.mjs';

const read = (name) => readFileSync(new URL(`./${name}`, import.meta.url), 'utf8');

/** Loader -> the module it lives in and the slice it must report. */
const LOADERS = [
  { fn: 'loadFiles', module: 'shared-files.mjs', slice: 'files' },
  { fn: 'chatLoadChannels', module: 'message-transport.mjs', slice: 'channels' },
  { fn: 'loadContractsForState', module: 'work-loop-actions.mjs', slice: 'contract filter' },
  { fn: 'chatLoadConversation', module: 'message-transport.mjs', slice: 'conversation' },
];

test('every slice the chip can name has a loader that owns it', () => {
  // The control, and the pairing: a slice nobody reports is a panel nobody watches.
  const named = new Set(LOADERS.map((l) => l.slice));
  for (const slice of OUT_OF_BAND_SLICES) {
    assert.ok(named.has(slice), `${slice} is declared out-of-band but no loader here owns it`);
  }
  assert.equal(named.size, OUT_OF_BAND_SLICES.length, 'the two lists have drifted apart');
});

test('a loader that swallows its own error reports before doing so', () => {
  // The assertion that would have caught the dead code. A catch that keeps prior data and says
  // nothing is indistinguishable from a fetch that succeeded and returned the same data.
  const missing = [];
  for (const { fn, module, slice } of LOADERS) {
    const source = read(module);
    const start = source.indexOf(`function ${fn}`);
    assert.notEqual(start, -1, `${fn} is gone from ${module}`);
    // The function body up to the next top-level export.
    const next = source.indexOf('\nexport ', start);
    const body = source.slice(start, next === -1 ? source.length : next);
    if (!/catch\s*\(/.test(body)) continue;          // propagates: the caller's catch is live
    if (!body.includes(`noteSliceFailure('${slice}')`)) missing.push(`${fn} in ${module}`);
  }
  assert.deepEqual(
    missing, [],
    'these loaders swallow their own failure without reporting it, so the connection chip cannot '
    + 'know the panel is stale: ' + missing.join('; '),
  );
});

test('the one loader that propagates is left to its caller', () => {
  // Named explicitly. If chatLoadConversation grows an internal catch, the rule above starts
  // applying to it and this test says why the two are treated differently.
  const source = read('message-transport.mjs');
  const start = source.indexOf('function chatLoadConversation');
  const next = source.indexOf('\nexport ', start);
  const body = source.slice(start, next === -1 ? source.length : next);
  assert.ok(
    !/catch\s*\(/.test(body),
    'chatLoadConversation now swallows its own error; it must report like the others, because the '
    + "poll cycle's catch no longer sees anything",
  );
});

test('the poll cycle still catches, as defence in depth', () => {
  // Not dead: it covers the propagating loader, and any loader that stops swallowing later. Pinned so
  // a tidy-up that removes it has to argue with this line.
  const cycle = read('refresh-cycle.mjs');
  assert.ok(cycle.includes("noteSliceFailure('conversation')"), 'the live call-site report was removed');
});
