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
 * the bar and pure black/white are reached only when a mid-tone accent needs them. If NONE clears
 * 4.5 -- possible for a genuine mid-grey, where no foreground can -- the best available is returned
 * rather than an arbitrary one, so the result is the most readable that colour permits.
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

// Compute the CSS custom-property overrides for a palette (pure — returns a {var:value} map).
export function derivePaletteVars(palette = {}) {
  const accent = normalizedHexColor(palette.accent, THEMES.default.accent);
  const second = normalizedHexColor(palette.secondary, THEMES.default.secondary);
  const third = normalizedHexColor(palette.tertiary, THEMES.default.tertiary);
  const contrast = contrastingForeground;
  const readable = (hex) => (hexLuminance(hex) > 0.38 ? hex : `color-mix(in srgb, ${hex} 64%, #ffffff)`);
  return {
    '--accent': accent,
    '--accent-strong': `color-mix(in srgb, ${accent} 72%, #000000)`,
    '--accent-hover': `color-mix(in srgb, ${accent} 82%, #ffffff)`,
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
