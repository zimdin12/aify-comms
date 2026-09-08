// Pairing two views of one output stream, and refusing when they cannot honestly be paired.
//
// EXTRACTED FROM `measure-console-latency.mjs` SO ITS COUNTEREXAMPLES CAN BE TESTS. That script opens
// two live streams, so nothing in it could be exercised without a running daemon and a running
// service -- and an independent review found two real defects in this logic by writing throwaway
// counterexamples against a copy of it. A defect found that way and fixed in place is a defect with
// nothing stopping it coming back.
//
// ── THE TWO DEFECTS, because they are the whole reason this file has a shape.
//
// A PROBE THAT APPEARS TWICE IS NOT AN ANCHOR. `indexOf` returns the FIRST match. Agent output
// repeats by nature -- spinners, progress bars, retried lines -- so the first match is routinely the
// wrong one. Measured counterexample: a true offset of 0 and a true lag of 50ms were reported as an
// offset of -300 and a lag of 350ms, with 300 confident pairs behind the number.
//
// A MISMATCH IS EVIDENCE, NOT NOISE. Skipping units that disagree turns a wrong alignment into a
// clean-looking result: one counterexample dropped 200 mismatches and reported 2,300 pairs at a
// confident 50ms. A disagreeing unit means the alignment is wrong or the two streams are not the
// same output; either way the timings are not about the units they name.
//
// UNITS ARE UTF-16 CODE UNITS. Both streams are decoded to strings before they are compared, so
// "unit" is the thing JavaScript indexes with. For ASCII the count equals bytes and for anything
// else it does not, which is why the word "byte" appears nowhere in the reported figures.

//: How much of the consumer stream to anchor on, and how far it may grow looking for a unique match.
//: A short probe is more likely to repeat; a long one is more likely to straddle a gap.
export const PROBE_MIN = 120;
export const PROBE_MAX = 2048;

//: A run with more than this share of disagreeing units is not describing one stream of output.
export const MISMATCH_LIMIT = 0.01;

/**
 * Where the consumer's buffer starts inside the producer's.
 *
 * @returns {{offset: number, probeSize: number} | {problem: string}}
 */
export function alignment(produced, delivered) {
  const source = String(produced ?? "");
  const target = String(delivered ?? "");
  if (target.length < PROBE_MIN || source.length < PROBE_MIN) {
    return { problem: "too little traffic to anchor on" };
  }
  const middle = Math.floor(target.length / 2);
  let everFound = false;
  for (let size = PROBE_MIN; size <= PROBE_MAX; size *= 2) {
    const probe = target.slice(middle, middle + size);
    // A PROBE SHORTER THAN THE MINIMUM cannot be grown any further -- there is no more stream to the
    // right of the middle -- so stop rather than testing the same short string repeatedly.
    if (probe.length < PROBE_MIN) break;
    const first = source.indexOf(probe);
    if (first === -1) break;
    everFound = true;
    // UNIQUE OR NOTHING. Equal first and last occurrence is the whole test.
    if (first === source.lastIndexOf(probe)) return { offset: first - middle, probeSize: probe.length };
  }
  return {
    problem: everFound
      ? "no unique anchor: every probe appears more than once in the producer stream"
      : "the two streams share no anchor",
  };
}

/**
 * Pair each consumer unit with the producer unit it corresponds to, and time the difference.
 *
 * @param {object} input
 * @param {string} input.produced   what the producer stream said
 * @param {string} input.delivered  what the consumer stream said
 * @param {number[]} input.producedAt  arrival time of each producer unit, same indexing
 * @param {number[]} input.deliveredAt arrival time of each consumer unit, same indexing
 * @param {number} input.offset     from `alignment`
 * @returns {{lags: number[], mismatched: number, unpairable: number, problem: string}}
 */
export function pairLags({ produced, delivered, producedAt, deliveredAt, offset }) {
  const lags = [];
  let mismatched = 0;
  let unpairable = 0;
  for (let i = 0; i < delivered.length; i += 1) {
    const p = i + offset;
    // OUTSIDE THE OVERLAP IS NOT A MISMATCH. The two streams were joined at different moments, so
    // their ends do not line up; those units are simply not comparable and are counted separately.
    if (p < 0 || p >= producedAt.length || p >= produced.length) { unpairable += 1; continue; }
    if (delivered[i] !== produced[p]) { mismatched += 1; continue; }
    lags.push(deliveredAt[i] - producedAt[p]);
  }
  const compared = lags.length + mismatched;
  const problem = compared > 0 && mismatched / compared > MISMATCH_LIMIT
    ? `${mismatched} of ${compared} compared units disagreed between the two streams`
    : "";
  return { lags, mismatched, unpairable, problem };
}

/** The distribution, or the reason there is none. Sorts a COPY: the caller's order is theirs. */
export function distribution(lags, { minimum = 100 } = {}) {
  if (!Array.isArray(lags) || lags.length < minimum) {
    return { problem: `only ${Array.isArray(lags) ? lags.length : 0} paired units; too few to report a distribution` };
  }
  const sorted = [...lags].sort((a, b) => a - b);
  const at = (q) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))];
  return {
    count: sorted.length,
    min: at(0),
    p50: at(0.5),
    p90: at(0.9),
    p99: at(0.99),
    max: sorted[sorted.length - 1],
    problem: "",
  };
}
