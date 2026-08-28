// WHICH FILES ARE "THE DOCTOR" -- derived, never listed.
//
// Four separate scanners each hardcoded `doctor.js` as the answer. Moving ONE check into its own
// module so a test could execute it reddened three of them at once and left the fourth quietly
// scanning a file the check no longer lived in. A list you must remember to update in four places is
// a defect with a delay on it, and the delay had already started: the fourth scanner was green
// because it had stopped looking, not because there was nothing to find.
//
// So: the doctor is `doctor.js` plus every local module it reaches, transitively. A check that moves
// into a new module is found with no edit anywhere.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Every file the doctor is built from, absolute, `doctor.js` first. */
export function doctorSourceFiles(entry = path.join(STDIO, "doctor.js")) {
  const seen = new Map();
  const walk = (file) => {
    if (seen.has(file)) return;
    const source = readFileSync(file, "utf-8");
    seen.set(file, source);
    for (const [, spec] of source.matchAll(/from\s+"(\.[^"]+)"/g)) {
      walk(path.resolve(path.dirname(file), spec));
    }
  };
  walk(entry);
  return [...seen.keys()];
}

/** Those files' text, joined -- what a scanner that used to read one file should read instead. */
export function doctorSourceText() {
  return doctorSourceFiles().map((f) => readFileSync(f, "utf-8")).join("\n");
}
