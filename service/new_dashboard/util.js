// Shared pure utilities for the Dashboard Next SPA.
// No DOM, no module-level mutable state — safe to import anywhere and unit-test directly.
// (DASHBOARD_REBUILD_PLAN §2 / §0.1: the ES-module split starts with the pure cores.)

// HTML-escape every interpolation, attribute contexts included (the 8800 stored-XSS lesson —
// plan §7). The 5-char map covers text and quoted-attribute contexts.
export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

// Parse the mixed timestamp shapes the API returns (epoch-ms ints, epoch-seconds, ISO-8601 text)
// to epoch-ms, or NaN if unparseable. Bare Date.parse() mis-reads a numeric epoch-ms (it stringifies
// the number → invalid date), so route everything through here for both display and sorting.
export const tsMs = (value) => {
  if (value === null || value === undefined || value === '') return NaN;
  let ms = Number(value);
  if (!Number.isFinite(ms) || String(value).includes('-')) ms = Date.parse(value);
  if (Number.isFinite(ms) && ms > 0 && ms < 1000000000000) ms *= 1000; // epoch-seconds → ms
  return ms;
};

// Relative "Nm / Nh / Nd ago" from an ISO string OR epoch (seconds or ms). Tolerant of the
// mixed timestamp shapes the API returns: epoch-ms ints, epoch-seconds, and ISO-8601 text.
// THE CLOCK IS AN ARGUMENT HERE, and that is the whole point of splitting it out. `relTime` reads
// `Date.now()` inside itself, so nothing can ask what a given moment renders as, and the ticker in
// `rel-time-ticker.mjs` needs exactly that: one reading of the clock, applied to every node on the
// page, so a screenful of times cannot disagree about what "now" is.
export const relTimeAt = (value, nowMs) => {
  if (!value) return '';
  const ms = tsMs(value);
  if (!Number.isFinite(ms)) return '';
  const minutes = Math.max(0, Math.round((nowMs - ms) / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 6) / 10;
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
};

export const relTime = (iso) => relTimeAt(iso, Date.now());

// The same label, plus the absolute instant it was computed from, so it can be re-rendered later
// WITHOUT re-rendering the section around it.
//
// `render-memo.mjs` states the problem it solves: a memoised section repaints when a FIELD moves, and
// "4m ago" has to move when no field does. Putting `lastSeen` in the signature repaints only when the
// heartbeat lands; putting a clock tick in it repaints every section on a timer and destroys the
// operator's selection and scroll while doing it.
//
// The timestamp shipped is the PARSED epoch-ms, never the raw field. The API returns epoch-seconds,
// epoch-ms and ISO text; resolving that once at render keeps the ticker a pure arithmetic step
// instead of re-running the tolerant parser against every node on every tick.
//
// EMPTY IN, EMPTY OUT -- not an empty span. Several call sites read `relTime(x) ? ... : ''`, and a
// zero-width span would make every one of those conditionals true and print a stray separator.
export const relTimeHtml = (value, nowMs = Date.now()) => {
  const label = relTimeAt(value, nowMs);
  if (!label) return '';
  return `<span class="rel-time" data-rel-ts="${tsMs(value)}">${esc(label)}</span>`;
};


// v0.5.4: three pure value-to-string formatters arrived from app.js, which could not test them — importing
// app.js executes module-scope browser code and throws before a test can reach anything in it.
//
// They belong here rather than in a new module because this file's docstring already claims exactly the
// property they have, and because `esc`/`tsMs`/`relTime` are the same kind of thing. Note the header above
// asserted "safe to import anywhere and unit-test directly" while NOTHING tested it — the claim was true
// and unenforced, which is how a true claim becomes a stale one. `util.test.mjs` now covers all six.

export function fileSizeLabel(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function usageResetLabel(iso) {
  try {
    const ms = new Date(iso) - new Date();
    if (!(ms > 0)) return 'resets soon';
    const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
    return h > 0 ? `resets in ${h}h ${m}m` : `resets in ${m}m`;
  } catch { return ''; }
}

export function usageFmtTokens(n) {
  n = Number(n || 0);
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}
