// The page titles and subtitles, moved out of app.js in v0.5.4.
//
// One entry per navigable page. `setPage` reads it to fill the header, falling back to `sessions` for an
// unknown key — so a page missing from here does not break navigation, it silently mislabels the header,
// which is the failure this being data rather than markup is meant to keep cheap to check.

export const pages = {
  chat: ['Chat', 'Direct messages and channels across the fleet — the operator landing surface.'],
  sessions: ['Sessions', 'Live terminal and lifecycle controls per session — messaging lives in Chat.'],
  environments: ['Environments', 'Connected bridges, runtimes, roots, and capacity.'],
  diagnostics: ['Work', 'Work-loop contracts and run/dispatch evidence.'],
  analytics: ['Analytics', 'Fleet-wide message traffic, run outcomes, and live capacity.'],
  files: ['Files', 'Shared artifacts (comms_share). Upload, download, and remove files.'],
  settings: ['Settings', 'Curated service and dashboard configuration. Saves apply to the live service.'],
};
