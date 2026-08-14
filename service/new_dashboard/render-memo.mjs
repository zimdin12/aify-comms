// Skip a re-render when nothing it depends on changed. Moved out of app.js in v0.5.4.
//
// The dashboard polls, so every section is asked to re-render on a timer whether or not its data moved.
// Rendering anyway is not merely wasteful: it destroys and rebuilds DOM under an operator who may be
// mid-selection, mid-scroll or mid-dropdown. This is the memo that stops that, and its correctness is
// entirely in the signature — too coarse and it re-renders constantly, too narrow and it goes blind to
// a real change.

export const _sectionSig = Object.create(null);

export function renderSection(key, signature, renderFn) {
  const sig = JSON.stringify(signature);
  if (_sectionSig[key] === sig) return;
  _sectionSig[key] = sig;
  renderFn();
}
