/**
 * How often would the RUNNING service make a browser console recover?
 *
 * WHY. `check-deployed-console-transport.py` shows the deployed queue numbering its frames per POST
 * rather than per FRAME, so a coalesced flush advances the sequence by more than one and the
 * dashboard reads a gap that nothing dropped. That says the mechanism EXISTS in the running code.
 * It says nothing about how often it fires, and that is the difference between a console that
 * recovers constantly and one that stutters occasionally.
 *
 * THIS IS NOT AN ATTRIBUTION OF THE OPERATOR'S REPORTED LAG, and an earlier version of this comment
 * called it "the mechanism behind" that lag, which claims exactly what has not been shown. It counts
 * one specific trigger, on one fleet, in one window. Nothing here observes a console, a repaint, or
 * a person waiting.
 *
 * THE RULE IS MIRRORED FROM ITS CONSUMER, NOT RE-DERIVED. `realtime-socket.mjs` is the only thing
 * that reads these numbers, and a hand-written "is this a gap" diverged from it in BOTH directions:
 * it lowered its cursor on a regression, so 10,5,11 counted a gap the browser never takes (the
 * browser leaves `lastSeq` at 10, and 11 is contiguous), while 10,5,6 counted one drop where the
 * browser drops two. The block below keeps that block's shape deliberately, so the two can be
 * diffed rather than argued about.
 *
 * WHAT IT DOES. Opens ONE read-only WebSocket to `/ws` -- no `agent_id`, so nothing binds to an
 * agent and `online_agents()` is untouched. It sends nothing.
 *
 *   node scripts/measure-live-frame-gaps.mjs [seconds]
 *
 * Exit 0 when it gathered enough COMPARISONS to answer, 2 when it did not.
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SECONDS = Number(process.argv[2] || 120);

/**
 * THE FLOOR IS ON COMPARISONS, NOT ON FRAMES, and that distinction is a real defect this replaces.
 * A run reading the wrong field produced forty frames that were all UNNUMBERED, made zero
 * within-terminal comparisons, and printed "0.0%" with exit 0 -- a frame floor cleared entirely by
 * frames that could not have revealed a gap if one existed. Forty terminals seen once each do the
 * same. Only a frame actually judged against that terminal's previous sequence counts here.
 */
const FLOOR = 20;

/**
 * THE DEPLOYED BATCHING PARAMETERS, read from the container rather than from this checkout:
 * `idle_flush_ms = 4`, `max_latency_ms = 24`. A batch coalesces exactly when a second post lands
 * within 4ms of the one before it, which is the whole condition the sequence defect needs.
 */
const IDLE_FLUSH_MS = 4;

function apiKey() {
  try {
    const out = execFileSync('bash', [path.join(ROOT, 'scripts', 'api-key.sh')],
      { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    const lines = out.trim().split('\n').filter(Boolean);
    return lines.length ? lines[lines.length - 1].trim() : '';
  } catch { return ''; }
}

/** Per terminal, the browser's own `entry.lastSeq`: -1 means UNKNOWN and skips both checks. */
const lastSeqOf = new Map();
/** Arrival times per terminal, so the distance from the coalescing threshold can be stated. */
const arrivedAt = new Map();
const gapsMs = [];

let frames = 0;          // every terminal_output event seen
let comparisons = 0;     // those actually judged against a previous sequence for the same terminal
let unnumbered = 0;      // no finite seq: the browser paints these without touching lastSeq
let dropped = 0;         // seq <= lastSeq: the browser discards, and does NOT lower its cursor
let recoveries = 0;      // seq > lastSeq + 1: the browser refetches, resets and repaints
let badSpans = 0;

/**
 * `realtime-socket.mjs`'s sequence block, mirrored. Returns the outcome by NAME so a control can
 * assert which branch ran rather than only that some total moved.
 */
function note(terminalId, seq, at) {
  frames += 1;
  if (at !== undefined) {
    const previous = arrivedAt.get(terminalId);
    arrivedAt.set(terminalId, at);
    // Finite and not negative, written positively: NaN fails every comparison, so a guard phrased
    // as "drop the negative ones" would admit it and quietly widen the distribution.
    if (previous !== undefined) {
      const span = at - previous;
      if (Number.isFinite(span) && span >= 0) gapsMs.push(span); else badSpans += 1;
    }
  }

  const finite = Number.isFinite(seq);
  const lastSeq = lastSeqOf.has(terminalId) ? lastSeqOf.get(terminalId) : -1;

  if (!finite) { unnumbered += 1; return 'unnumbered'; }
  if (lastSeq >= 0) {
    comparisons += 1;
    // THE CURSOR IS NOT LOWERED HERE. The browser returns without touching `lastSeq`, so a late
    // frame is discarded and the NEXT one is still judged against the high-water mark.
    if (seq <= lastSeq) { dropped += 1; return 'dropped'; }
    if (seq > lastSeq + 1) { recoveries += 1; lastSeqOf.set(terminalId, seq); return 'recovery'; }
  }
  lastSeqOf.set(terminalId, seq);
  return 'painted';
}

/**
 * THE CONTROL, ASSERTING EACH OUTCOME BY NAME.
 *
 * A total-only control credits the wrong event: replace the gap test with `seq === last + 1` and it
 * counts the CONSECUTIVE frame while missing the +3 one, yet "one gap was found" still passes and
 * the instrument is now measuring the opposite thing. Each case below names the branch it must
 * take, so a predicate that fires on the wrong frame fails here instead of in publication.
 */
function selfTest() {
  const before = { frames, comparisons, unnumbered, dropped, recoveries };
  const mark = '__control__';
  const cases = [
    [10, 'painted', 'the first frame establishes the cursor and is not compared'],
    [11, 'painted', 'a consecutive frame is painted, never counted as a gap'],
    [14, 'recovery', 'a +3 step is the gap the browser refetches on'],
    [12, 'dropped', 'a frame behind the cursor is discarded'],
    [15, 'painted', 'and that drop did NOT lower the cursor, so 15 follows 14'],
    [NaN, 'unnumbered', 'a frame with no finite sequence reaches neither branch'],
  ];
  const wrong = [];
  for (const [seq, want, why] of cases) {
    const got = note(mark, seq);
    if (got !== want) wrong.push(`${seq}: expected ${want} (${why}), got ${got}`);
  }
  frames = before.frames; comparisons = before.comparisons; unnumbered = before.unnumbered;
  dropped = before.dropped; recoveries = before.recoveries;
  lastSeqOf.delete(mark);

  if (wrong.length) {
    console.log('  CONTROL FAILED, so nothing below would mean anything:');
    for (const line of wrong) console.log(`    ${line}`);
    return false;
  }
  console.log(`  CONTROL: all ${cases.length} cases took the branch they name, including the drop `
    + 'that must not lower the cursor.');
  return true;
}

async function main() {
  if (!selfTest()) process.exit(2);

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

  // A SOCKET THAT DIES MID-WINDOW OBSERVES NOTHING AFTER IT, and would report the partial window as
  // the whole one -- a restart at second 10 of a 360s run leaves a verdict covering 350 seconds
  // nobody watched. The window has to be closed by the TIMER, not by the transport.
  let closedEarly = null;
  socket.addEventListener('close', (e) => { closedEarly ??= { code: e.code, at: Date.now() }; });
  socket.addEventListener('error', () => { closedEarly ??= { code: 'error', at: Date.now() }; });

  console.log(`WATCHING the live service for ${SECONDS}s. Read-only: no agent_id, nothing sent.`);
  socket.addEventListener('message', (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload?.event !== 'terminal_output') return;
    const body = payload.data ?? payload;
    // THE FIELD IS `seq`, which is what the only consumer reads (`Number(data.seq)`). An earlier
    // version read `outputSeq` -- the name the HTTP snapshot uses -- so every frame came back
    // unnumbered while the summary printed a clean 0.0%.
    note(String(body.terminalId ?? '?'), Number(body.seq), performance.now());
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
  console.log(`  frames seen            ${frames}   across ${lastSeqOf.size} terminal(s)`);
  console.log(`  COMPARISONS made       ${comparisons}   (judged against that terminal's own previous seq)`);
  console.log(`  unnumbered frames      ${unnumbered}   (no finite seq: neither branch is reached)`);
  console.log(`  dropped (seq <= last)  ${dropped}`);
  console.log(`  RECOVERIES triggered   ${recoveries}   (seq > last + 1: refetch, reset, repaint)`);
  console.log('');

  if (badSpans) {
    console.log(`UNKNOWN: ${badSpans} arrival interval(s) were not finite non-negative durations,`);
    console.log('so the clock this run measured with cannot be trusted for the rest of it.');
    process.exit(2);
  }

  if (comparisons < FLOOR) {
    console.log(`UNKNOWN: ${comparisons} comparisons is below the ${FLOOR} this run needs -- ${frames}`);
    console.log('frames arrived but few were judged against a previous sequence for the same');
    console.log('terminal, and a gap can only be seen where a comparison happened. Re-run while an');
    console.log('agent is producing output.');
    process.exit(2);
  }

  const rate = (100 * recoveries / comparisons).toFixed(1);
  console.log(`${recoveries} of ${comparisons} compared frames (${rate}%) would make the browser`);
  console.log('refetch, reset and repaint.');

  if (gapsMs.length) {
    const sorted = gapsMs.slice().sort((a, b) => a - b);
    const at = (q) => sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))];
    const under = sorted.filter((ms) => ms < IDLE_FLUSH_MS).length;
    console.log('');
    console.log(`  per-terminal arrival intervals (n=${sorted.length}):`);
    console.log(`    p50 ${at(0.5).toFixed(1)}ms   p05 ${at(0.05).toFixed(1)}ms   min ${sorted[0].toFixed(1)}ms`);
    console.log(`    under the ${IDLE_FLUSH_MS}ms idle flush: ${under}  -- these could coalesce`);
    console.log('');
    if (recoveries) {
      console.log('  (recoveries were seen, so these intervals are a LOWER bound on the post rate:');
      console.log('   a coalesced flush hides the posts inside it.)');
    } else {
      // THE MINIMUM IS THE NUMBER THAT MATTERS. "Sometimes" is a claim about the BUSIEST moment,
      // and a median far from the threshold hides a minimum sitting just outside it.
      console.log(`  Nothing coalesced. THE CLOSEST APPROACH was ${sorted[0].toFixed(1)}ms against the `
        + `${IDLE_FLUSH_MS}ms window -- ${(sorted[0] / IDLE_FLUSH_MS).toFixed(1)}x outside it, so a`);
      console.log('  burst only modestly faster than anything here would start batching.');
      console.log(`  The TYPICAL terminal posted every ${at(0.5).toFixed(0)}ms, `
        + `${(at(0.5) / IDLE_FLUSH_MS).toFixed(0)}x too slow, which is why a quiet fleet can run`);
      console.log('  this build all day and see nothing.');
    }
  }

  console.log('');
  console.log('WHAT THIS DOES NOT ESTABLISH: how many POSTS each flush carried. Zero steps over one');
  console.log('is consistent with every flush carrying exactly one post, and equally consistent with');
  console.log('a queue that numbers frames correctly -- the wire alone cannot separate those two. It');
  console.log('also counts TRIGGERS rather than repaints: no console, fetch or reset was observed.');
  process.exit(0);
}

main();
