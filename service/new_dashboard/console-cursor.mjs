// Where a console's cursor is, and what is being held while nobody knows.
//
// THREE CONSUMERS, ONE SET OF RULES. The socket holds frames that must not be painted yet
// (`realtime-socket.mjs`), the recovery places them against a snapshot (`console-actions.mjs`), and
// the MOUNT does the same thing under a different name -- it resets the terminal and paints a
// server-rendered snapshot over whatever arrived while its fetch was in flight (`xterm-mount.mjs`).
// Two of those three had their own copy of the sequence read and only one of them had been
// corrected, which is how review found a repaired recovery sitting beside an unrepaired mount doing
// the identical thing.
//
// THE RULES THEMSELVES ARE ABOUT ONE QUESTION: is this console's position KNOWN? A known position
// may only advance over bytes that were actually written; an unknown one cannot be adjacent to
// anything and must not be treated as a zero. Every defect these functions carry a note about is a
// version of answering that question with a fallback instead of a value.

//: How many frames may be held while a console recovers. A frame is one WS payload, and a recovery
//: is one HTTP round trip: on the measured path that is a handful. This is a bound against a
//: recovery that never returns, not a tuning knob -- past it the console falls back to resuming from
//: the snapshot alone, which is what it did before any of this existed.
const MAX_HELD_FRAMES = 512;

/**
 * Put one frame aside for the recovery to place, or mark the queue overflowed.
 *
 * ONE PLACE, because there are now three reasons to hold: a frame that arrived GAPPED, any frame
 * that arrived while a recovery's fetch was outstanding, and any frame that arrived while a MOUNT
 * was fetching the snapshot it is about to reset the screen to. They must bound and overflow
 * identically -- two copies of a cap is a cap that eventually disagrees with itself.
 *
 * BOUNDED, because a recovery that never finishes must not grow memory. Past the cap the queue is
 * abandoned and a marker is left: the drain then resumes from the snapshot alone, which is what the
 * console did before frames were held at all and therefore never worse.
 */
export function holdFrame(entry, seq, output) {
  if (!Array.isArray(entry.pendingFrames)) entry.pendingFrames = [];
  if (entry.pendingFrames.length >= MAX_HELD_FRAMES) {
    entry.pendingFrames = [];
    entry.pendingOverflowed = true;
    return;
  }
  entry.pendingFrames.push({ seq, output: String(output) });
}

/**
 * Where the console is after painting this snapshot, read as a VALUE rather than through a fallback.
 *
 * A NULL SEQUENCE IS THE SERVER SAYING UNKNOWN, AND `??` HEARS IT AS "ASK SOMEBODY ELSE".
 *
 * The chain `outputSeq ?? seq ?? current` selects the FALLBACK on null, so a console whose cursor
 * was 4 stayed at 4 while the server had just said it does not know where the screen is. Review
 * caught it in the recovery, and then caught the identical chain still live in the mount -- and also
 * caught why the first test missed it: that test started at -1, so it could not detect a failed
 * transition from KNOWN to unknown. The null has to be read as a value, which is what `in` does and
 * `??` cannot.
 *
 * ABSENT IS NOT UNKNOWN. A response that never mentions a sequence says nothing about the console's
 * position, so the caller's current one stands; a response that says `null` says the server does not
 * know where the screen is, and -1 is how this console spells that.
 *
 * @param {object} terminal the `terminal` object from a console GET
 * @param {number} current the cursor to keep when the response does not mention one
 * @returns {number} the cursor to adopt, -1 meaning UNKNOWN
 */
export function cursorFromSnapshot(terminal, current) {
  const answered = terminal ?? {};
  const told = 'outputSeq' in answered ? answered.outputSeq
    : ('seq' in answered ? answered.seq : undefined);
  return told === null ? -1 : Number(told === undefined ? current : told);
}

//: HOW MUCH OF THE PAINTED STREAM IS KEPT. `consoleAwaitingInputHint` reads the last 400
//: characters, so 600 leaves room for the escape sequences that share those bytes without
//: holding a console's whole history in memory per entry.
const REMEMBERED_CHARS = 600;

/**
 * Remember bytes that were just painted, so the await pill can still see them.
 *
 * WHATEVER PAINTS, REMEMBERS. `entry.recentText` is the only input to
 * `consoleAwaitingInputHint`, and it used to be written on ONE of the four paths that write to
 * the terminal -- the socket's live branch. A snapshot repaint and a drained frame both left it
 * stale, so a console whose agent printed its prompt during a recovery showed the prompt and no
 * pill. Harmless while the drain was rare; the mount hold routes every frame of a newly opened
 * console through it, which is exactly the quiet-agent case the pill is for.
 */
export function rememberPainted(entry, text) {
  entry.recentText = (String(entry.recentText || '') + String(text)).slice(-REMEMBERED_CHARS);
}
/**
 * Paint the frames the socket held while a fetch was in flight, and resume from them.
 *
 * WITHOUT THIS THE RECOVERY LOOPS. The snapshot carries the buffer as it stood when the SERVER
 * answered; frames past that arrived during the fetch. Resuming from the snapshot's sequence leaves
 * the console behind the stream, so the next live frame is another gap, another fetch, another
 * window in which nothing paints. Reproduced against the real socket and this function in
 * `the-console-does-not-stall-while-it-resyncs.test.mjs`.
 *
 * ALREADY-COVERED FRAMES ARE DISCARDED rather than replayed. The snapshot is a RENDERED SCREEN, so a
 * frame at or below its sequence is already in the picture; writing it again would paint bytes twice
 * -- which for a TUI is not a duplicate line, it is a cursor somewhere nobody asked for.
 *
 * AN OVERFLOWED QUEUE REPLAYS NOTHING. Whatever was dropped is genuinely lost to this console, and
 * pretending otherwise by painting the tail would put the screen out of order. Resuming from the
 * snapshot alone is what happened before frames were held at all, so the fallback is never worse
 * than the behaviour it replaced -- one more gap, one more recovery, and it settles.
 *
 * @returns {boolean} whether frames are still held that this pass could not place, which is the
 *   caller's signal that the recovery is UNRESOLVED and owes another fetch.
 */
export function drainHeldFrames(entry) {
  const held = Array.isArray(entry.pendingFrames) ? entry.pendingFrames : [];
  entry.pendingFrames = [];
  if (entry.pendingOverflowed) { entry.pendingOverflowed = false; return false; }
  const replay = held
    .filter((f) => Number.isFinite(f?.seq) && f.seq > entry.lastSeq)
    .sort((a, b) => a.seq - b.seq);
  // ONLY AN ADJACENT RUN, and this is the whole correctness of the replay.
  //
  // THE DEFECT THIS CLOSES, found by the whole-diff review 2026-09-08, and it is the worst shape a
  // console defect can take: silent, permanent, and self-concealing. The filter above admits every
  // held frame ABOVE the floor, so a snapshot at 5 with 9 and 10 held painted both and advanced
  // `lastSeq` to 10 -- while 6, 7 and 8 had never been painted. Each of them then arrives with
  // `seq <= lastSeq` and is discarded as already covered, and frame 11 looks adjacent to 10, so no
  // gap is ever detected and no second recovery happens. The screen is missing three frames for the
  // life of the console, and nothing anywhere reports it.
  //
  // SO THE SEQUENCE MAY ONLY ADVANCE OVER BYTES THAT WERE ACTUALLY WRITTEN. A held frame that does
  // not continue the picture stops the replay and leaves `lastSeq` where the screen really ends --
  // the next arriving frame then reads as the gap it is and recovers, which is one more round trip
  // and a correct console instead of a fast wrong one.
  //
  // A DUPLICATE IS WRITTEN ONCE. The socket holds whatever arrived, retransmits included, and for a
  // TUI painting the same bytes twice is not a doubled line -- it is a cursor somewhere nobody asked
  // for.
  //
  // AND WHAT IT COULD NOT PLACE IS KEPT, which the first version of this fix destroyed. It emptied
  // `pendingFrames` at the top and then broke out of the loop, so the un-replayed tail was gone --
  // review's case is a snapshot at 5 with 9 and 10 held: the break was correct, dropping 9 and 10
  // was not, and the caller then marked the recovery finished. On a quiet agent no further frame
  // ever arrives to notice, so the console sits believing it is live with output it was handed and
  // threw away. The remainder stays queued and the caller fetches again.
  // AN UNKNOWN POSITION CANNOT BE ADJACENT TO ANYTHING, and requiring it to be threw the frames
  // away. Review's trace: a null snapshot leaves `lastSeq` at -1, the run below demands the first
  // held frame be seq 0, no real frame ever is, and the queue is cleared at the end of this
  // function -- three fetches, nothing painted, output gone, and nothing left to notice it.
  //
  // WITH NO POSITION THERE IS NOTHING TO CONTRADICT, so the held run is painted from its lowest
  // sequence and the console adopts that position. It may repaint something the snapshot already
  // held; it cannot drop anything, and this project's rule is that dropping is the worse failure.
  // After the first frame the position is KNOWN again and the adjacency rule resumes for the rest,
  // which is why this is a seed rather than a mode.
  let index = 0;
  if (entry.lastSeq < 0 && replay.length) {
    const first = replay[0];
    try {
      entry.term.write(first.output);
      rememberPainted(entry, first.output);
      entry.lastSeq = first.seq;
      index = 1;
    } catch { index = 0; }
  }
  for (; index < replay.length; index += 1) {
    const f = replay[index];
    if (f.seq <= entry.lastSeq) continue;
    if (f.seq !== entry.lastSeq + 1) break;
    try { entry.term.write(f.output); } catch { break; }
    rememberPainted(entry, f.output);
    entry.lastSeq = f.seq;
  }
  const remainder = replay.slice(index).filter((f) => f.seq > entry.lastSeq);
  entry.pendingFrames = remainder;
  return remainder.length > 0;
}
