// Shared pure utilities for the Dashboard Next SPA.
// No DOM, no module-level mutable state — safe to import anywhere and unit-test directly.
// (DASHBOARD_REBUILD_PLAN §2 / §0.1: the ES-module split starts with the pure cores.)

// HTML-escape every interpolation, attribute contexts included (the 8800 stored-XSS lesson —
// plan §7). The 5-char map covers text and quoted-attribute contexts.
export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

// Relative "Nm / Nh / Nd ago" from an ISO string OR epoch (seconds or ms). Tolerant of the
// mixed timestamp shapes the API returns: epoch-ms ints, epoch-seconds, and ISO-8601 text.
export const relTime = (iso) => {
  if (!iso) return '';
  let ms = Number(iso);
  if (!Number.isFinite(ms) || String(iso).includes('-')) ms = Date.parse(iso);
  if (Number.isFinite(ms) && ms > 0 && ms < 1000000000000) ms *= 1000;
  if (!Number.isFinite(ms)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 6) / 10;
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
};
