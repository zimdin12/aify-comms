// Nothing reacts to an error that cannot arrive.
//
// A function that swallows its own error returns normally on failure. A caller that wraps it in
// `try { ... } catch { react }` therefore never reacts, and the reaction is dead — a handler wired to
// an event that does not occur. It is invisible in review because both halves read as careful.
//
// This repo has now produced it three times in three different places, twice by me:
//
//   * the interrupt attribution queried a table nothing writes (1a3de61a, fixed in ff0ac8f5);
//   * noteSliceFailure was added to four poll-cycle catches, three of which cannot run (85780f7a,
//     fixed in dd80bb33);
//   * requestBulkSessionControl toasts `${action} ${id} failed` in a catch that never runs, so a
//     bulk stop over twenty sessions reported "Session stop failed" with NO id — the code that
//     named the failing session was unreachable. Found by sweeping for the pattern rather than by
//     tripping over it.
//
// So the pattern is swept for, not remembered. The sweep is deliberately narrow: a catch whose body
// is only a comment is a documented no-op, and an empty `catch {}` is belt-and-braces. What fails
// here is a catch that DOES something in reaction to an error that cannot reach it.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

const HERE = new URL('./', import.meta.url);
const SOURCES = Object.fromEntries(
  readdirSync(HERE)
    .filter((f) => (f.endsWith('.mjs') || f.endsWith('.js')) && !f.includes('.test.'))
    .map((f) => [f, readFileSync(new URL(f, HERE), 'utf8')]),
);

/** Exported functions that catch and never re-throw: calling one cannot fail. */
function swallowers() {
  const found = {};
  for (const [name, text] of Object.entries(SOURCES)) {
    for (const m of text.matchAll(/export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/g)) {
      const next = text.indexOf('\nexport ', m.index + 1);
      const body = text.slice(m.index, next === -1 ? text.length : next);
      if (/catch\s*[({]/.test(body) && !/\bthrow\b/.test(body)) found[m[1]] = name;
    }
  }
  return found;
}

/** Call sites that react to an error from a swallower. */
function deadReactions() {
  const swallows = swallowers();
  const out = [];
  for (const [name, text] of Object.entries(SOURCES)) {
    text.split(String.fromCharCode(10)).forEach((line, i) => {
      const m = /try\s*\{\s*(?:await\s+)?([A-Za-z_$][\w$]*)\(/.exec(line);
      if (!m || !(m[1] in swallows)) return;
      const tail = line.slice(m.index + m[0].length);
      if (!tail.includes('catch')) return;
      const after = tail.slice(tail.indexOf('catch'));
      // THE CATCH'S OWN braces, found by balancing rather than by lastIndexOf. The naive version
      // ran to the last '}' on the line, so `if (x) { try { f(); } catch {} }` reported the IF's
      // closing brace as the catch body and flagged an empty catch as a live reaction. It failed
      // on correct code, which is the one thing a gate must never do.
      const open = after.indexOf('{');
      if (open === -1) return;
      let depth = 0;
      let close = -1;
      for (let k = open; k < after.length; k += 1) {
        if (after[k] === '{') depth += 1;
        else if (after[k] === '}') { depth -= 1; if (depth === 0) { close = k; break; } }
      }
      if (close === -1) return;
      const body = after.slice(open + 1, close);
      const acting = body.replace(/\/\*[\s\S]*?\*\//g, '').trim();
      if (acting) out.push({ file: name, line: i + 1, fn: m[1], acts: acting.slice(0, 60) });
    });
  }
  return out;
}

test('the sweep can see both halves', () => {
  // The control. Zero swallowers, or zero call sites, would make the assertion below vacuous — the
  // exact wrong-zero this review has produced five times.
  const s = swallowers();
  assert.ok(Object.keys(s).length > 20, `only ${Object.keys(s).length} swallowers found`);
  assert.equal(s.loadFiles, 'shared-files.mjs', 'the sweep no longer recognises a known swallower');
  assert.ok(!('zzzNotAFunction' in s));
});

test('no reaction is wired to an error that cannot arrive', () => {
  // The three poll-cycle catches are the deliberate exception: dd80bb33 moved the real report into
  // each loader and kept these as defence in depth for a loader that stops swallowing later. They
  // are listed by NAME so the exemption cannot quietly widen.
  const allowed = new Set([
    'refresh-cycle.mjs:loadContractsForState',
    'refresh-cycle.mjs:chatLoadChannels',
    'refresh-cycle.mjs:loadFiles',
  ]);
  const offenders = deadReactions()
    .filter((d) => !allowed.has(`${d.file}:${d.fn}`))
    .map((d) => `${d.file}:${d.line} reacts to ${d.fn}() which cannot fail -> ${d.acts}`);
  assert.deepEqual(
    offenders, [],
    'these catches react to an error the callee already swallowed: ' + offenders.join('; '),
  );
});

test('a bulk session failure names the session that failed', () => {
  // The case this sweep found. The bulk loop stops twenty sessions; the toast that identified the
  // failing one lived in a catch that never runs, so the operator saw an unattributed failure.
  const source = SOURCES['agent-session-actions.mjs'];
  assert.match(
    source, /toast\(`Session \$\{sessionId\} \$\{action\} failed/,
    'the failure toast no longer names the session, so a bulk failure is unattributable again',
  );
});
