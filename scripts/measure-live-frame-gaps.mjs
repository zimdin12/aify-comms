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
 * The deployed idle flush, read from the container: a batch coalesces when a second post lands
 * within 4ms of the one before it. NAMED ONLY SO THE PROSE BELOW CAN SAY WHAT IT IS NOT
 * COMPARING AGAINST -- no margin is derived from arrival intervals any more, because those are
 * receiver-side and a post-to-arrival path runs through the queue, the flush timer, the event
 * loop and this process.
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
let gapEvents = 0;       // every seq > lastSeq + 1 on the wire -- the UPPER bound on recoveries
let recoveries = 0;      // distinct recovery EPISODES, holding gaps that arrive while one is pending
const pending = new Set();  // terminals whose recovery this model treats as still in flight
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
    if (seq > lastSeq + 1) {
      gapEvents += 1;
      // A GAP WHILE A RECOVERY IS PENDING IS HELD, NOT A SECOND RECOVERY. The browser sets
      // `entry.resyncing` and the next gapped frame takes `holdFrame(...)` and RETURNS -- so
      // 10,14,18 initiates ONE recovery and holds the rest, where counting every gap reported two.
      // This models the pending window as "until a contiguous frame arrives", which is an
      // ASSUMPTION about when the fetch lands and is stated as one: the true window is an HTTP
      // round trip nothing here observes, so this is a LOWER bound on episodes and gapEvents is
      // the upper one.
      if (!pending.has(terminalId)) { recoveries += 1; pending.add(terminalId); }
      lastSeqOf.set(terminalId, seq);
      return 'recovery';
    }
  }
  pending.delete(terminalId);
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
  const before = { frames, comparisons, unnumbered, dropped, recoveries, gapEvents };
  const mark = '__control__';
  const cases = [
    [10, 'painted', 'the first frame establishes the cursor and is not compared'],
    [11, 'painted', 'a consecutive frame is painted, never counted as a gap'],
    [14, 'recovery', 'a +3 step is the gap the browser refetches on'],
    [12, 'dropped', 'a frame behind the cursor is discarded'],
    [15, 'painted', 'and that drop did NOT lower the cursor, so 15 follows 14'],
    [NaN, 'unnumbered', 'a frame with no finite sequence reaches neither branch'],
  ];
  // THE COUNTER'S CONTRIBUTION, NOT ONLY THE BRANCH LABEL. Deleting `recoveries += 1` while
  // keeping the predicate, the cursor update and `return 'recovery'` passed every case here and
  // then published zero recoveries against a real +3 wire step. A name is not a count.
  const counters = () => ({recoveries, gapEvents, dropped, unnumbered, comparisons});
  const wrong = [];
  for (const [seq, want, why] of cases) {
    const before = counters();
    const got = note(mark, seq);
    const after = counters();
    if (got !== want) { wrong.push(`${seq}: expected ${want} (${why}), got ${got}`); continue; }
    const moved = Object.keys(after).filter((k) => after[k] !== before[k]);
    const owed = {recovery: ['recoveries', 'gapEvents'], dropped: ['dropped'],
                  unnumbered: ['unnumbered'], painted: []}[want];
    for (const counter of owed) {
      if (!moved.includes(counter)) {
        wrong.push(`${seq}: took the ${want} branch but never incremented ${counter} -- `
          + `a branch label is not a published count`);
      }
    }
  }
  // AND THE PENDING-RECOVERY HOLD, which is its own case because it is the half where this model
  // and the browser most easily diverge: 10, 14, 18 is TWO wire gaps and ONE recovery episode,
  // since the browser sets `resyncing` on the first and holds the second.
  const hold = '__control_hold__';
  const beforeHold = {recoveries, gapEvents};
  for (const seq of [10, 14, 18]) note(hold, seq);
  const episodes = recoveries - beforeHold.recoveries;
  const events = gapEvents - beforeHold.gapEvents;
  if (episodes !== 1 || events !== 2) {
    wrong.push(`10,14,18 must be ${2} wire gaps and ${1} recovery episode; got ${events} and `
      + `${episodes} -- the pending-recovery hold is not modelled`);
  }

  frames = before.frames; comparisons = before.comparisons; unnumbered = before.unnumbered;
  dropped = before.dropped; recoveries = before.recoveries; gapEvents = before.gapEvents;
  lastSeqOf.delete(mark);
  lastSeqOf.delete(hold);
  pending.delete(mark);
  pending.delete(hold);

  if (wrong.length) {
    console.log('  CONTROL FAILED, so nothing below would mean anything:');
    for (const line of wrong) console.log(`    ${line}`);
    return false;
  }
  console.log(`  CONTROL: ${cases.length} branch cases plus the pending-recovery hold, each `
    + 'asserted on the counter it publishes rather than on its label.');
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
  console.log(`  wire GAPS (seq > last+1) ${gapEvents}   the UPPER bound on recoveries`);
  console.log(`  RECOVERY EPISODES      ${recoveries}   gaps arriving while one is pending are HELD`);
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
  console.log(`${recoveries} recovery episode(s) across ${comparisons} compared frames (${rate}%),`);
  console.log(`from ${gapEvents} wire gap(s). The episode count assumes a recovery stays pending`);
  console.log('until a contiguous frame arrives, which is a MODEL of the fetch window rather than');
  console.log('an observation of it -- so episodes are a lower bound and wire gaps an upper one.');

  if (gapsMs.length) {
    const sorted = gapsMs.slice().sort((a, b) => a - b);
    const at = (q) => sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))];
    console.log('');
    console.log(`  intervals between FRAME ARRIVALS at this receiver (n=${sorted.length}):`);
    console.log(`    p50 ${at(0.5).toFixed(1)}ms   p05 ${at(0.05).toFixed(1)}ms   min ${sorted[0].toFixed(1)}ms`);
    console.log('');
    // THESE ARE ARRIVALS, NOT POSTS, AND NO MARGIN IS DERIVED FROM THEM ANY MORE.
    //
    // The removed version compared this minimum against the 4ms idle flush and published a
    // "2.1x outside the coalescing window" margin. That inference does not hold: what is timed
    // here is when a BROADCAST reached this socket, and between a host's POST and that arrival sit
    // the queue, the flush timer, the event loop, the WebSocket and this process's own scheduling.
    // Driven at synthetic 1ms spacing the old text printed "0.3x outside" and "0x too slow"; at
    // 4000ms it still called a burst "only modestly faster" while printing 1000x. Both are the
    // arithmetic of a quantity that was never post spacing.
    //
    // What the intervals DO bound is this receiver's own view, which is worth printing and worth
    // nothing more than that.
    console.log('  WHAT THESE ARE NOT: the spacing of the POSTS that produced them. Between a');
    console.log('  host POST and a frame arriving here sit the write queue, its flush timer, the');
    console.log('  event loop, the WebSocket and this receiver scheduling. Nothing here bounds');
    console.log('  how close two posts came to sharing a flush window, so no margin against the');
    console.log('  4ms idle flush is derived from them -- an earlier version did, and was wrong.');
  }

  console.log('');
  console.log('WHAT THIS DOES NOT ESTABLISH: how many POSTS each flush carried. Zero steps over one');
  console.log('is consistent with every flush carrying exactly one post, and equally consistent with');
  console.log('a queue that numbers frames correctly -- the wire alone cannot separate those two. It');
  console.log('also counts TRIGGERS rather than repaints: no console, fetch or reset was observed.');
  process.exit(0);
}

main();
