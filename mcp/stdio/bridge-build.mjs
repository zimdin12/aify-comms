// WHICH CODE IS ACTUALLY RUNNING — the twelve characters an operator can paste to prove it.
//
// Every deploy path in this repo fails silently (see CLAUDE.md): a container serving the previous build, a
// wrapper still executing the copy it loaded at boot, an install that copied but was never relaunched. The
// build tag exists so a banner, a diagnostics string and the control plane's `bridgeBuild` can each name the
// commit they are running, instead of everyone inferring it from the absence of an error.
//
// TWO SOURCES, AND THE ORDER IS THE WHOLE POINT. `install.sh` copies the bridge into `~/.aify-comms/` and
// stamps `.aify-version` there with the repo sha at copy time — the native copy has NO `.git`, so the stamp
// is the only evidence available in a normal install. A repo checkout has no stamp, so it falls back to
// reading `.git/HEAD` (following a ref, then `packed-refs`). Stamp first, git second, and every failure
// answers with a WORD rather than throwing: `no-git`, `unknown-ref`, `unknown`. A build tag that could throw
// would take down the thing it was added to describe.
//
// WHY THIS IS AN OWNER RATHER THAN A HELPER. There were TWO implementations of it — `computeBridgeBuildTag`
// in `server.js` and `readBuildTag` in `runtimes-exec.js` — and they had diverged. The second was the same
// algorithm MINUS the `.aify-version` branch, which is the fix recorded for 2026-06-10 as
// "every installed bridge printed no-git and the banner couldn't prove which code runs". That fix was
// applied to one copy and not the other.
//
// Measured on the live install rather than reasoned about: `~/.aify-comms` has `.aify-version`
// (`short=577c7ca`) and no `.git`, and `diagnosticsFor()` loaded from that copy reported `build=no-git`
// while the banner reported the stamped sha. So the ONE string whose purpose is proving which code runs
// could not do it, in the install shape that is normal. Unifying on the complete implementation fixes that
// consumer by construction, which is the argument for an owner over a shared helper: a second copy of a
// derivation is a second thing to forget.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

export function computeBridgeBuildTag() {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    // Native-copy install (the normal case): install.sh stamps .aify-version at the
    // install root (two levels up, ~/.aify-comms/.aify-version) with the repo SHA at
    // copy time — the native copy has no .git, so without this every installed bridge
    // printed "no-git" and the banner couldn't prove which code runs (2026-06-10).
    const stampPath = path.resolve(here, "..", "..", ".aify-version");
    if (fs.existsSync(stampPath)) {
      const m = fs.readFileSync(stampPath, "utf-8").match(/^short=(\S+)/m)
        || fs.readFileSync(stampPath, "utf-8").match(/^sha=(\S+)/m);
      if (m && m[1] && m[1] !== "unknown") return m[1].slice(0, 12);
    }
    // Repo-checkout fallback: read .git/HEAD two levels up.
    const gitDir = path.resolve(here, "..", "..", ".git");
    const headPath = path.join(gitDir, "HEAD");
    if (!fs.existsSync(headPath)) return "no-git";
    const head = fs.readFileSync(headPath, "utf-8").trim();
    if (head.startsWith("ref:")) {
      const refPath = path.join(gitDir, head.slice(4).trim());
      if (fs.existsSync(refPath)) {
        return fs.readFileSync(refPath, "utf-8").trim().slice(0, 12);
      }
      // packed-refs fallback
      const packed = path.join(gitDir, "packed-refs");
      if (fs.existsSync(packed)) {
        const lines = fs.readFileSync(packed, "utf-8").split(/\r?\n/);
        const refName = head.slice(4).trim();
        for (const line of lines) {
          if (line.endsWith(refName)) return line.split(/\s+/)[0].slice(0, 12);
        }
      }
      return "unknown-ref";
    }
    return head.slice(0, 12);
  } catch {
    return "unknown";
  }
}

// Resolved once at load. It describes the code on disk, which cannot change under a running process, so a
// second read would only cost another stat.
export const BRIDGE_BUILD_TAG = computeBridgeBuildTag();
