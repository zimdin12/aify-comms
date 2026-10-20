// The file-set manifest a registration spec binds (guided install S1): one `sha256sum` line per file.
//
// PURE and import-free beyond node:crypto, so the stamp-time spec CLI can compute a manifest without
// loading any module that can resolve a credential.

import { createHash } from "node:crypto";

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/**
 * `<64 hex>  <relative/path>` per file, LF, sorted by path, so `sha256sum -c` can check a materialised
 * copy. A path that could escape the copy, or break the line format, is refused.
 *
 * @param {{path: string, bytes: Buffer}[]} files
 */
export function manifestText(files) {
  const lines = files.map(({ path, bytes }) => {
    if (!path || /[\r\n\\]/.test(path) || path.startsWith("/") || path.split("/").includes("..")) {
      throw new Error(`manifest: unusable path ${JSON.stringify(path)}`);
    }
    return { path, line: `${sha256(bytes)}  ${path}\n` };
  });
  lines.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  return lines.map(({ line }) => line).join("");
}
