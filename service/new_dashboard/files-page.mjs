// Whether the Files page is on screen, and therefore whether its data is worth fetching.
//
// WHY THIS EXISTS. `state.files` has exactly one reader -- the Files page -- but the poll cycle
// fetched `/shared` every time it ran, open or not. Measured against the live service on 2026-08-25:
// 113,854 bytes for 388 files, 34,839 gzipped, once per cycle per open tab. A dashboard left on the
// Chat page all day downloaded a file list nobody could see, about 24 MB an hour per tab after
// compression.
//
// THE PAGE STATE LIVES IN THE DOM, not in `state`. `setPage` toggles an `active` class on
// `#page-<name>` and keeps no field, so this reads the same thing the renderers do -- several of
// them already no-op on a hidden host for the same reason. A second copy of "which page is open"
// held in state would be a new thing to keep in sync with the class that actually decides it.
//
// IT FAILS CLOSED, deliberately: no document, no element, no classList -- anything that means "I
// cannot tell" answers YES, fetch. The cost of a wrong yes is one request nobody reads. The cost of
// a wrong no is a Files page showing a stale list, or an empty one, with nothing to correct it.

/**
 * True when the Files page is open, OR when its state cannot be determined.
 *
 * @param {Document|null} doc  injected so a test can pin it; defaults to the real document when there
 *   is one, and to null under Node, where "cannot tell" is the honest answer.
 */
/**
 * True when the page with this id is open, OR when its state cannot be determined.
 *
 * Generalised from `shouldLoadFiles` when a SECOND page-gated slice turned up. Measured on the live
 * service 2026-08-26: one poll cycle moves 1,419,728 bytes, and `/spawn-requests` is 414,690 of it --
 * the single largest item, rendered only by the Environments page's table. `/shared` was 113,854.
 * Two slices, one rule, so the fail-closed reasoning lives in one place instead of being retyped.
 *
 * @param {string} pageId  the `data-page` name, e.g. "files" or "environments"
 * @param {Document|null} doc  injected so a test can pin it
 */
export function shouldLoadForPage(pageId, doc = (typeof document === "undefined" ? null : document)) {
  const id = String(pageId || "").trim();
  if (!id) return true;
  const el = doc && typeof doc.getElementById === "function" ? doc.getElementById(`page-${id}`) : null;
  // Missing element or missing classList is not evidence of a closed page.
  if (!el || !el.classList || typeof el.classList.contains !== "function") return true;
  return el.classList.contains("active");
}

export function shouldLoadFiles(doc = (typeof document === "undefined" ? null : document)) {
  return shouldLoadForPage("files", doc);
}
