/**
 * How often does the RUNNING service emit a frame sequence the browser reads as a gap?
 *
 * WHY THIS IS THE MISSING NUMBER. `check-deployed-console-transport.py` proves the deployed queue
 * counts POSTS rather than frames, which is the mechanism behind the operator's "our browser
 * terminal kind of lags sometimes". It does not say how OFTEN that fires, and that is the whole
 * difference between a console that recovers constantly and one that stutters occasionally. The
 * harness chose a stride; this counts the real one.
 *
 * WHAT IT DOES. Opens ONE read-only WebSocket to `/ws` -- no `agent_id`, so nothing is bound to an
 * agent and `online_agents()` is untouched -- and watches `terminal_output` events go by, applying
 * the browser's own rule: `seq > lastSeq + 1` is a gap, and a gap costs a full recovery (an HTTP
 * refetch, a `term.reset()`, and a whole-screen repaint). It sends nothing and changes nothing.
 *
 * WHY A ZERO HERE WOULD MEAN NOTHING WITHOUT THE CONTROL. A quiet fleet emits no frames at all, and
 * "no gaps in zero frames" reads exactly like "no gaps". So the frame COUNT is reported first and a
 * run that saw too few frames refuses to draw a conclusion. That is the failure this repo keeps
 * finding in its own instruments, and it is the one a passive observer is most exposed to.
 *
 *   node scripts/measure-live-frame-gaps.mjs [seconds]
 *
 * Exit 0 when it gathered enough frames to answer, 2 when it did not.
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SECONDS = Number(process.argv[2] || 120);

/** Enough frames that "no gaps" is a finding rather than an empty room. */
const FLOOR = 20;

function apiKey() {
  try {
    const out = execFileSync('bash', [path.join(ROOT, 'scripts', 'api-key.sh')],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    const lines = out.trim().split('\n').filter(Boolean);
    return lines.length ? lines[lines.length - 1].trim() : '';
  } catch { return ''; }
}

/**
 * THE DEPLOYED BATCHING PARAMETERS, read from the container rather than from this checkout, because
 * the threshold that matters is the RUNNING one:
 *
 *   idle_flush_ms  = 4    a flush fires 4ms after the last post, if no further post arrives
 *   max_latency_ms = 24   and at most 24ms after the batch's first post
 *
 * So a batch COALESCES exactly when a second post lands within 4ms of the one before it. That is
 * the condition the sequence defect needs, and it is why a fleet can run a defective build all day
 * without the browser ever seeing a gap.
 */
const IDLE_FLUSH_MS = 4;

/** Per terminal: the last sequence seen, the frames counted, and the gaps the browser would act on. */
const seen = new Map();
/** Arrival times per terminal, so the distance from the coalescing threshold can be stated. */
const arrivedAt = new Map();
const gapsMs = [];
let badSpans = 0;
let frames = 0;
let unnumbered = 0;
let regressed = 0;
const gaps = [];

function note(terminalId, seq, at) {
  frames += 1;
  if (at !== undefined) {
    const previous = arrivedAt.get(terminalId);
    arrivedAt.set(terminalId, at);
    // FINITE AND NOT NEGATIVE, for the reason the projection probe's spans are admitted the same
    // way: a guard phrased as "drop the negative ones" admits NaN, because NaN fails every
    // comparison. A clock that misbehaves must refuse, never quietly widen the distribution.
    if (previous !== undefined) {
      const span = at - previous;
      if (Number.isFinite(span) && span >= 0) gapsMs.push(span); else badSpans += 1;
    }
  }
  if (seq === null || seq === undefined) { unnumbered += 1; return; }
  const last = seen.get(terminalId);
  seen.set(terminalId, seq);
  if (last === undefined) return;          // the first frame for a terminal establishes the baseline
  if (seq <= last) { regressed += 1; return; }
  if (seq > last + 1) gaps.push({ terminalId, from: last, to: seq, step: seq - last });
}

/**
 * THE DETECTOR, DRIVEN BOTH WAYS BEFORE IT IS BELIEVED.
 *
 * A gap counter that reports 0 looks identical whether the fleet is clean or the counter is broken
 * -- and this one WAS broken on its first run, reading `outputSeq` where the wire says `seq`. So the
 * run proves the instrument on synthetic frames first: it must COUNT a jump, IGNORE a consecutive
 * pair, and notice a regression. A live zero means nothing until this passes.
 */
function selfTest() {
  const before = { frames, unnumbered, regressed, gaps: gaps.length };
  const mark = '__control__';
  note(mark, 10);            // baseline, counts no gap
  note(mark, 11);            // consecutive -- must NOT be a gap
  note(mark, 14);            // +3 -- MUST be a gap
  note(mark, 12);            // backwards -- a regression, not a gap
  note(mark, null);          // unnumbered
  const found = {
    gap: gaps.length - before.gaps === 1,
    quiet: true,
    regressed: regressed - before.regressed === 1,
    unnumbered: unnumbered - before.unnumbered === 1,
  };
  // Undo the control so it cannot reach the reported figures.
  frames = before.frames; unnumbered = before.unnumbered; regressed = before.regressed;
  gaps.length = before.gaps;
  seen.delete(mark);
  const ok = found.gap && found.regressed && found.unnumbered;
  console.log(ok
    ? '  CONTROL: the detector counted the +3 jump, the regression and the unnumbered frame, and'
    + ' let the consecutive pair pass.'
    : `  CONTROL FAILED: ${JSON.stringify(found)}`);
  return ok;
}

async function main() {
  if (!selfTest()) {
    console.log('UNKNOWN: the gap detector cannot detect a gap, so a live zero would mean nothing.');
    process.exit(2);
  }
  const key = apiKey();
  const url = `ws://localhost:8800/ws${key ? `?api_key=${encodeURIComponent(key)}` : ''}`;
  const socket = new WebSocket(url, { headers: { Origin: 'http://localhost:8811' } });

  const opened = await new Promise((resolve) => {
    socket.addEventListener('open', () => resolve(true), { once: true });
    socket.addEventListener('error', () => resolve(false), { once: true });
    socket.addEventListener('close', () => resolve(false), { once: true });
  });
  if (!opened) {
    console.log('UNKNOWN: the WebSocket did not open, so nothing was observed.');
    process.exit(2);
  }

  // A SOCKET THAT DIES MID-WINDOW OBSERVES NOTHING AFTER IT, AND WOULD REPORT THE PARTIAL WINDOW
  // AS THE WHOLE ONE. Self-review found this: a service restart at second 10 of a 360s run leaves
  // a frame count that clears the floor and a "0 gaps" verdict covering 350 seconds nobody watched.
  // The window has to be closed by the TIMER, not by the transport.
  let closedEarly = null;
  socket.addEventListener('close', (e) => { closedEarly ??= { code: e.code, at: Date.now() }; });
  socket.addEventListener('error', () => { closedEarly ??= { code: 'error', at: Date.now() }; });

  console.log(`WATCHING the live service for ${SECONDS}s. Read-only: no agent_id, nothing sent.`);
  socket.addEventListener('message', (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload?.event !== 'terminal_output') return;
    const body = payload.data ?? payload;
    // THE FIELD IS `seq`, READ FROM THE WIRE AND CROSS-CHECKED AGAINST ITS ONLY CONSUMER.
    // My first version read `outputSeq` -- the name the HTTP snapshot uses -- and every one of
    // 291 frames came back unnumbered, which the gap counter would have reported as a clean
    // 0.0%. `realtime-socket.mjs` does `const seq = Number(data.seq)`, so that is the rule.
    note(String(body.terminalId ?? '?'), Number.isFinite(Number(body.seq)) ? Number(body.seq) : null,
      performance.now());
  });

  const startedAt = Date.now();
  await new Promise((resolve) => setTimeout(resolve, SECONDS * 1000));
  const early = closedEarly;
  socket.close();

  if (early) {
    const watched = ((early.at - startedAt) / 1000).toFixed(0);
    console.log('');
    console.log(`UNKNOWN: the socket closed after ${watched}s of a ${SECONDS}s window `
      + `(${early.code}), so the rest was not observed and a verdict would cover time nobody`);
    console.log('watched. Re-run against a service that stays up.');
    process.exit(2);
  }

  console.log('');
  console.log(`  frames seen            ${frames}   across ${seen.size} terminal(s)`);
  console.log(`  unnumbered frames      ${unnumbered}   (an unnumbered chunk clears the number)`);
  console.log(`  regressed sequences    ${regressed}   (the browser DROPS these outright)`);
  console.log(`  GAPS the browser acts on ${gaps.length}`);
  console.log('');

  if (frames < FLOOR) {
    console.log(`UNKNOWN: ${frames} frames is below the ${FLOOR} this run needs. A quiet fleet`);
    console.log('produces no frames, and "no gaps in almost none" is not evidence of no gaps.');
    console.log('Re-run while an agent is actually producing output.');
    process.exit(2);
  }

  const rate = (100 * gaps.length / frames).toFixed(1);
  console.log(`${gaps.length} of ${frames} frames (${rate}%) carried a step over one, and EACH of`);
  console.log('those costs the browser a refetch, a reset and a whole-screen repaint.');
  if (gaps.length) {
    const worst = gaps.slice().sort((a, b) => b.step - a.step).slice(0, 5);
    console.log('');
    console.log('  largest steps:');
    for (const g of worst) console.log(`    ${g.terminalId}: ${g.from} -> ${g.to}  (+${g.step})`);
  }
  // HOW FAR THIS FLEET IS FROM THE CONDITION THE DEFECT NEEDS.
  //
  // ONLY VALID IN THE ZERO-GAP REGIME, and that is why it is printed after the gap count rather
  // than beside it: an inter-FRAME interval equals an inter-POST interval only while nothing is
  // coalescing, because a coalesced flush hides the posts inside it. With gaps observed, this
  // becomes a lower bound on the post rate and says so.
  if (badSpans) {
    console.log(`UNKNOWN: ${badSpans} arrival interval(s) were not finite non-negative durations,`);
    console.log('so the clock this run measured with cannot be trusted for the rest of it.');
    process.exit(2);
  }

  if (gapsMs.length) {
    const sorted = gapsMs.slice().sort((a, b) => a - b);
    const at = (q) => sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))];
    const under = sorted.filter((ms) => ms < IDLE_FLUSH_MS).length;
    console.log(`  per-terminal arrival intervals (n=${sorted.length}):`);
    console.log(`    p50 ${at(0.5).toFixed(1)}ms   p05 ${at(0.05).toFixed(1)}ms   `
      + `min ${sorted[0].toFixed(1)}ms`);
    console.log(`    under the ${IDLE_FLUSH_MS}ms idle flush: ${under}`
      + `  -- these are the ones that could coalesce`);
    // THE MINIMUM IS THE NUMBER THAT MATTERS, not the median. "Sometimes" is a claim about the
    // BUSIEST moment, and a median 69x from the threshold sounds like a fleet nowhere near it while
    // the fastest interval observed can be sitting just outside. Leading with the median would
    // understate the risk in exactly the direction that matters.
    const nearest = sorted[0] / IDLE_FLUSH_MS;
    const typical = at(0.5) / IDLE_FLUSH_MS;
    console.log('');
    if (gaps.length) {
      console.log('  (gaps were seen, so these intervals are a LOWER bound on the post rate.)');
    } else {
      console.log(`  Nothing coalesced. THE CLOSEST APPROACH was ${sorted[0].toFixed(1)}ms against `
        + `the ${IDLE_FLUSH_MS}ms window -- ${nearest.toFixed(1)}x outside it, so a burst only `
        + `modestly faster than anything seen here would start batching and start faking gaps.`);
      console.log(`  The TYPICAL terminal posted every ${at(0.5).toFixed(0)}ms, ${typical.toFixed(0)}x `
        + `too slow, which is why a quiet fleet can run this build all day and see nothing.`);
    }
  }

  console.log('');
  console.log('THIS IS A RATE, NOT AN ATTRIBUTION OF ANY PARTICULAR STUTTER. It says how often the');
  console.log('deployed sequence gives the browser a reason to recover, on this fleet, in this window.');
  process.exit(0);
}

main();
