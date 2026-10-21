// An attempt's output folder, acquired exclusively (review section 6.2). Creating the folder itself, not recursively,
// is the claim: the operating system lets exactly one caller create a name, so two attempts can never both own one
// folder and overwrite each other's attempt.json or scenario evidence. A folder that already exists, empty or not, is
// another attempt's, and is refused.

import fs from "node:fs";
import path from "node:path";

export class AttemptDirTaken extends Error {}

/** Create `outDir` for this attempt alone, or throw AttemptDirTaken naming it. Its parent may be created freely. */
export function acquireAttemptDir(outDir) {
  fs.mkdirSync(path.dirname(outDir), { recursive: true });
  try {
    fs.mkdirSync(outDir);
  } catch (error) {
    if (error?.code === "EEXIST") throw new AttemptDirTaken(`${outDir} already exists: another attempt owns it`);
    throw error;
  }
  return outDir;
}
