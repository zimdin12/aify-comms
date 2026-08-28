// theme.js — WS-A dashboard theming/coloring engine.
//
// Ported + modernized from the old 8800 dashboard's appearance system. Drives:
//   - 8 named presets via document.body.dataset.theme (CSS in styles.css)
//   - a custom 3-color palette (primary/secondary/tertiary) applied as CSS vars
//   - document.title + sidebar brand text from dashboard_title
//
// Pure helpers (THEMES, paletteFromSettings, normalizedHexColor, hexLuminance,
// derivePaletteVars) are exported for unit tests; applyTheme/cache touch the DOM +
// localStorage so the chosen theme paints instantly on next load, before /settings returns.

export const THEMES = {
  default:  { label: 'Default dark', accent: '#51c5b0', secondary: '#74b7ff', tertiary: '#dfb156' },
  forest:   { label: 'Forest',       accent: '#49b87e', secondary: '#e4bb69', tertiary: '#8bbdf5' },
  violet:   { label: 'Violet',       accent: '#b9a5ff', secondary: '#f095c0', tertiary: '#60c78d' },
  ember:    { label: 'Ember',        accent: '#f2b76e', secondary: '#e78776', tertiary: '#8dbcf6' },
  ocean:    { label: 'Ocean',        accent: '#52d1d5', secondary: '#70b8ff', tertiary: '#67c987' },
  graphite: { label: 'Graphite',     accent: '#a9b5bc', secondary: '#89b9e8', tertiary: '#d0b66c' },
  crimson:  { label: 'Crimson',      accent: '#d34b64', secondary: '#8ebaf1', tertiary: '#e0bc64' },
  indigo:   { label: 'Indigo',       accent: '#8ea7ff', secondary: '#dd90bd', tertiary: '#66c889' },
};

const PALETTE_KEY = 'aifyDashboardPalette';
const THEME_KEY = 'aifyDashboardTheme';
const TITLE_KEY = 'aifyDashboardTitle';

export function themeKey(value) {
  const key = String(value || 'default').trim() || 'default';
  return Object.prototype.hasOwnProperty.call(THEMES, key) ? key : 'default';
}

export function normalizedHexColor(value, fallback) {
  const color = String(value || '').trim();
  return /^#[0-9a-fA-F]{6}$/.test(color) ? color.toLowerCase() : fallback;
}

export function hexLuminance(hex) {
  const value = normalizedHexColor(hex, '#ffffff').slice(1);
  const rgb = [0, 2, 4].map((i) => {
    const c = parseInt(value.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
}

// Resolve the effective palette: explicit settings colors win, else local cache, else preset.
export function paletteFromSettings(settings = {}, key = 'default', localPalette = {}) {
  const preset = THEMES[themeKey(key)] || THEMES.default;
  return {
    accent: normalizedHexColor(settings.dashboard_primary_color || localPalette.dashboard_primary_color, preset.accent),
    secondary: normalizedHexColor(settings.dashboard_secondary_color || localPalette.dashboard_secondary_color, preset.secondary),
    tertiary: normalizedHexColor(settings.dashboard_tertiary_color || localPalette.dashboard_tertiary_color, preset.tertiary),
  };
}

/** WCAG 2.x contrast ratio between two hex colours. 21 for black on white, 1 for a colour on itself. */
export function contrastRatio(a, b) {
  const [x, y] = [hexLuminance(a), hexLuminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** WCAG AA for normal-size text. `button.primary` renders 14px at weight 750 -- bold, not "large". */
export const MIN_CONTRAST = 4.5;

/**
 * The foreground for text sitting ON `hex`, chosen by MEASURED contrast rather than by brightness.
 *
 * THE BUG THIS REPLACES. It was `hexLuminance(hex) > 0.45 ? dark : light`, a brightness threshold used
 * as a proxy for contrast. Brightness is not contrast: five of the eight shipped themes came out below
 * WCAG AA on their own primary buttons, INCLUDING the default at 2.03:1 -- accent #51c5b0 is bright
 * enough to look light but not bright enough for white text, and 0.45 put it just on the wrong side.
 *
 * It matters more than the eight named themes: the operator can set an arbitrary custom accent in
 * Settings, and this function is the only thing standing between that colour and unreadable buttons.
 *
 * The candidates are tried in order, so the house near-black and near-white win whenever they clear
 * the bar and pure black/white are reached only when a mid-tone accent needs them. `#767676`
 * (luminance 0.181) is exactly that case: both house colours fall short and pure black gives 4.62.
 *
 * SOMETHING ALWAYS CLEARS 4.5, which an earlier version of this comment denied. Against a background
 * of luminance L, black gives (L + 0.05) / 0.05 and clears 4.5 when L >= 0.175; white gives
 * 1.05 / (L + 0.05) and clears it when L <= 0.1833. Those intervals OVERLAP, so for every valid
 * colour at least one of the two passes. The "best available" return below is therefore defensive
 * against a malformed input, NOT an accepted sub-AA outcome, and no caller should read it as
 * permission to ship one.
 */
export function contrastingForeground(hex) {
  const candidates = ['#06110f', '#f7fbff', '#000000', '#ffffff'];
  let best = candidates[0];
  let bestRatio = 0;
  for (const candidate of candidates) {
    const ratio = contrastRatio(candidate, hex);
    if (ratio >= MIN_CONTRAST) return candidate;
    if (ratio > bestRatio) { best = candidate; bestRatio = ratio; }
  }
  return best;
}

/**
 * The hover background: the accent moved 18% AWAY from the text drawn on it.
 *
 * IT USED TO ALWAYS MOVE TOWARD WHITE, which is fine when the text is dark and wrong when it is light
 * -- a lighter background under white text is LESS readable, and `.chat-scroll-bottom` keeps
 * `--accent-contrast` while swapping only its background on hover. Swept 5832 accents: 820 failed the
 * hover state, worst 3.22:1 at #5a7887, every one of them passing the resting state that the
 * foreground was chosen against.
 *
 * REQUIRING ONE FOREGROUND TO CLEAR BOTH STATES DOES NOT WORK, and trying it is what showed why. For a
 * mid-tone accent, dark text fails on the accent and light text fails on the lighter hover, so no
 * candidate clears both -- 715 pairs still failed. The single-background overlap proof (black clears
 * 4.5 above L=0.175, white below L=0.1833) says nothing about a PAIR spanning that gap.
 *
 * Moving away from the text instead makes hover contrast >= resting contrast by construction, so a
 * foreground chosen for the resting state is correct for both and the token stays single.
 */
export function hoverBackground(accent, foreground = contrastingForeground(accent)) {
  const towardBlack = hexLuminance(foreground) > hexLuminance(accent);
  return towardBlack ? towardWhite(accent, -0.18) : towardWhite(accent, 0.18);
}

/** The panel every `--accent-text` surface is mixed into. Fixed; the tints below are not. */
export const PANEL = '#15191b';

/** A non-mine message card, which the action hovers sit on. Lighter than the panel, so it binds. */
export const CHAT_SURFACE = '#161b1e';

/**
 * Every background `--accent-text` is actually drawn on, for THIS accent.
 *
 * A FIXED CONSTANT CANNOT BOUND THESE, which is what the first version got wrong. It measured against
 * `#1d2325` and called it "the lightest surface", but two of the four consumer surfaces are mixed FROM
 * the accent and therefore move with it: `.chat-chip.active` sits on `--accent-soft`
 * (`color-mix(accent 18%, panel)`) and `.chat-msg-mine` on a 12% tint. A light accent makes them
 * lighter than any constant chosen in advance.
 *
 * The counterexample, reproduced: accent `#a08088` cleared the constant at 4.50056:1 and therefore kept
 * the raw accent as its text colour -- which is 3.91:1 on the active chip and 4.31:1 on the mine
 * message. Substituting one constant for a population is the same error as certifying the static CSS
 * while the runtime overwrote it, one token further down.
 *
 * `.settings-tab.active` sits on the panel -- verified by resolving the rendered ancestor's computed
 * background rather than assuming it, which is how the omission below was made in the first place.
 *
 * `--chat-surface` WAS MISSING and is the fourth. The action hovers (`.chat-msg-reply/.detail/.act`)
 * take `--accent-text` on EVERY message card, and a non-mine card is `--chat-surface`, not the panel
 * -- an earlier version of this comment asserted panel for them and was simply wrong. Custom accent
 * `#5a001e` cleared all three enumerated surfaces (4.508 to 4.530) and rendered 4.43:1 on that one.
 */
export function accentTextSurfaces(accent) {
  return [PANEL, CHAT_SURFACE, mixInto(accent, 0.18, PANEL), mixInto(accent, 0.12, PANEL)];
}

/** `color-mix(in srgb, a p%, b)`, resolved — the same sRGB interpolation the stylesheet performs. */
function mixInto(a, percent, b) {
  const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [ar, ag, ab] = channels(a);
  const [br, bg, bb] = channels(b);
  const c = (x, y) => Math.round(x * percent + y * (1 - percent)).toString(16).padStart(2, '0');
  return `#${c(ar, br)}${c(ag, bg)}${c(ab, bb)}`;
}

/** Mix `hex` toward white by `percent` of white, in sRGB — the same space `color-mix(in srgb, …)` uses. */
function towardWhite(hex, percent) {
  const value = hex.replace('#', '');
  const channels = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
  // A NEGATIVE percent moves toward BLACK, which is what `hoverBackground` needs when the text is
  // light: the interpolation target flips rather than the caller doing its own arithmetic.
  const mixed = channels.map((c) => (percent >= 0
    ? Math.round(c + (255 - c) * percent)
    : Math.round(c * (1 + percent))));
  return `#${mixed.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

/**
 * An accent lightened until it is READABLE as text, rather than lightened by a fixed amount.
 *
 * THE BUG THIS REPLACES was the same shape as the button-contrast one, one token over:
 * `hexLuminance(hex) > 0.38 ? hex : color-mix(in srgb, hex 64%, #ffffff)`. A brightness threshold
 * picked the branch, and the dark branch then moved a FIXED 36% toward white -- which is not a
 * contrast guarantee. I claimed it was structurally floored; that argument covered only the raw-accent
 * branch, and review showed the mixed one has no floor at all. A valid operator accent of `#000000`
 * produced `#5c5c5c`, which is 2.65:1 on the panel -- well below AA, on `.chat-chip.active`,
 * `.settings-tab.active`, the mine-message heading and the action hovers.
 *
 * The eight shipped themes never hit it. The operator-controlled domain does, which is the whole
 * point: Settings accepts any hex, and a threshold cannot know what it will be handed.
 *
 * Measured against `LIGHTEST_SURFACE` because a light foreground has its WORST ratio on the lightest
 * background it is drawn on -- checking the darkest would flatter every candidate.
 */
export function readableAccentText(hex, backgrounds = accentTextSurfaces(hex)) {
  const against = [].concat(backgrounds);
  const worst = (colour) => Math.min(...against.map((bg) => contrastRatio(colour, bg)));
  if (worst(hex) >= MIN_CONTRAST) return hex;
  for (let percent = 0.05; percent < 1; percent += 0.05) {
    const candidate = towardWhite(hex, percent);
    if (worst(candidate) >= MIN_CONTRAST) return candidate;
  }
  return '#ffffff';
}

// Compute the CSS custom-property overrides for a palette (pure — returns a {var:value} map).
export function derivePaletteVars(palette = {}) {
  const accent = normalizedHexColor(palette.accent, THEMES.default.accent);
  const second = normalizedHexColor(palette.secondary, THEMES.default.secondary);
  const third = normalizedHexColor(palette.tertiary, THEMES.default.tertiary);
  const readable = (hex) => readableAccentText(hex);
  const contrast = contrastingForeground;
  // RESOLVED, not a `color-mix()` string, so it can be measured against the foreground drawn on it.
  // A value the deriving code cannot read is a value it cannot honour.
  const hover = hoverBackground(accent);
  return {
    '--accent': accent,
    '--accent-hover': hover,
    '--accent-text': readable(accent),
    '--accent-contrast': contrast(accent),
    '--secondary': second,
    '--secondary-text': readable(second),
    '--tertiary': third,
    '--tertiary-text': readable(third),
  };
}

function readLocal(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    return raw;
  } catch {
    return fallback;
  }
}

function readLocalPalette() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PALETTE_KEY) || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeLocalPalette(palette) {
  try {
    localStorage.setItem(PALETTE_KEY, JSON.stringify({
      dashboard_primary_color: normalizedHexColor(palette.accent, THEMES.default.accent),
      dashboard_secondary_color: normalizedHexColor(palette.secondary, THEMES.default.secondary),
      dashboard_tertiary_color: normalizedHexColor(palette.tertiary, THEMES.default.tertiary),
    }));
  } catch { /* storage may be unavailable; theming still applies for the session */ }
}

// Apply theme + palette + title to the live document. `persist` also caches for instant next-load.
export function applyTheme(settings = {}, { persist = true } = {}) {
  const key = themeKey(settings.dashboard_theme);
  const palette = paletteFromSettings(settings, key, readLocalPalette());
  const title = String(settings.dashboard_title || readLocal(TITLE_KEY, 'AIFY Comms') || 'AIFY Comms').trim() || 'AIFY Comms';

  document.body.dataset.theme = key;
  const vars = derivePaletteVars(palette);
  for (const [name, value] of Object.entries(vars)) document.body.style.setProperty(name, value);

  document.title = title;
  const brand = document.querySelector('.brand-copy strong');
  if (brand) brand.textContent = title;

  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, key);
      localStorage.setItem(TITLE_KEY, title);
    } catch { /* ignore */ }
    writeLocalPalette(palette);
  }
  return { key, palette, title };
}

// Paint the cached theme synchronously at startup, before settings are fetched, so the
// dashboard never flashes the default palette for a themed install.
export function applyCachedTheme() {
  applyTheme({
    dashboard_theme: readLocal(THEME_KEY, 'default'),
    dashboard_title: readLocal(TITLE_KEY, 'AIFY Comms'),
  }, { persist: false });
}

// Preview a live (unsaved) selection from the Appearance editor inputs.
export function previewTheme({ theme, primary, secondary, tertiary } = {}) {
  applyTheme({
    dashboard_theme: theme,
    dashboard_primary_color: primary,
    dashboard_secondary_color: secondary,
    dashboard_tertiary_color: tertiary,
  }, { persist: false });
}
