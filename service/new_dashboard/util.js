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
export const relTime = (iso) => {
  if (!iso) return '';
  const ms = tsMs(iso);
  if (!Number.isFinite(ms)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 6) / 10;
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
};
