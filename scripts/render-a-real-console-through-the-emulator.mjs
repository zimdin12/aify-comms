// Does aify-env's VT emulator produce a correct screen from a REAL console this service stored?
//
// WHY IT EXISTS. B1-B5 -- the renderer this version was built for -- are PASSES IN TESTS against
// synthetic fixtures, and whether they have ever run on the operator's fleet is UNVERIFIED: the
// aify-env daemon serving this host reports a boot build (`3b2bf8f9`) that differs from the code
// on disk (`1c36464c`), so it is not running what is there now. That does NOT establish which
// files it lacked -- an earlier version of this comment deduced 'never had the module' from a
// commit date, which dates the commit and not the file. Restarting it is the operator's action.
//
// This is the strongest evidence available WITHOUT that restart: take a console this service really
// stored, feed it to the real `ScreenEmulator`, render it with the real `screenLines`, and look at
// what a reader would see. It is still PASSES IN TESTS -- nothing here is the deployed path -- but
// it is real bytes rather than a fixture somebody wrote to pass.
//
//     node scripts/render-a-real-console-through-the-emulator.mjs <terminal.json> [--json]
//
// where `<terminal.json>` is the raw body of `GET /api/v1/terminals/{id}`, saved by curl.
//
// NODE PARSES THE RAW RESPONSE, AND THAT IS LOAD-BEARING. The first version of this ran
// curl -> python -> a scratch file -> node, and the python hop re-encoded: the render came out full
// of mojibake where box-drawing belongs, and 265 "lone surrogates" appeared that the raw body does
// not contain. That was one step from being reported as a garbled-console defect in the SERVICE.
// The raw body carries proper UTF-8 -- `e2 94 80` for U+2500 -- so the pipeline is curl -> file ->
// node with nothing in between that re-encodes. Control the extractor, not just the comparison.
//
// READ-ONLY, and it starts nothing. The input is a file. Importing aify-env's `lib/` modules loads
// two pure modules; it does not touch `bin/`, which would RUN the daemon and supersede the one
// serving this host.

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { homedir } from "node:os";
import { join } from "node:path";

const LIB = process.env.AIFY_ENV_REPO
  ? join(process.env.AIFY_ENV_REPO, "lib")
  : join(homedir(), "projects", "aify-env", "lib");

const capture = process.argv[2];
if (!capture) {
  console.error("usage: node scripts/render-a-real-console-through-the-emulator.mjs <terminal.json>");
  process.exit(2);
}

const { ScreenEmulator } = await import(pathToFileURL(join(LIB, "screen-emulator.mjs")).href);
const { screenLines, screenIsBlank } = await import(
  pathToFileURL(join(LIB, "screen-render.mjs")).href);

const answered = JSON.parse(readFileSync(capture, "utf8"));
const terminal = answered.terminal || answered;
const bytes = terminal.output || "";
const cols = Number(terminal.cols) || 80;
const rows = Number(terminal.rows) || 24;

if (!bytes) {
  console.error(`${capture} carries no terminal output, so nothing was rendered`);
  process.exit(2);
}

/** Characters that are not text: a screen holding any is a screen a reader cannot trust. */
const countUnprintable = (text) => [...text].filter((c) => {
  const code = c.codePointAt(0);
  return (code < 32 && c !== "\n" && c !== "\t") || code === 127
    || (code >= 0xd800 && code <= 0xdfff);
}).length;

const screen = await ScreenEmulator.create({ cols, rows });
if (!screen) {
  // THE OPTIONAL DEPENDENCY IS ABSENT, which is a configuration and not a failure. Exit 3 so a
  // caller can tell "no emulator here" from "the emulator produced something wrong".
  console.error("no emulator on this host (@xterm/headless absent), so nothing was rendered");
  process.exit(3);
}

// AWAITED, BECAUSE `write` RESOLVES WHEN THE PARSER HAS APPLIED THE BYTES. The first version slept
// 250ms instead, which is the exact mistake `screen-emulator.mjs` warns about in its own docstring:
// "every caller has to await this or it will render one chunk behind, which looks like lag and is
// actually a missing await". It happened to be long enough, so the screen was right -- but the
// number was not: `writeMs` was reported as the emulator's cost and was mostly my own sleep.
const startedWrite = Date.now();
const applied = await screen.write(bytes);
const writeMs = Date.now() - startedWrite;
if (!applied) {
  console.error("the emulator did not apply the bytes (disposed, or a stale generation)");
  process.exit(1);
}

const startedRead = Date.now();
const cells = screen.rows();
const lines = screenLines(cells, { width: cols, height: rows });
const readMs = Date.now() - startedRead;
screen.dispose();

const rendered = lines.join("\n");
const report = {
  geometry: `${cols}x${rows}`,
  runtime: terminal.runtime || "",
  inputChars: bytes.length,
  inputEscapeSequences: (bytes.match(/\[/g) || []).length,
  inputUnprintable: countUnprintable(bytes),
  writeMs,
  readMs,
  blank: screenIsBlank(cells),
  lineCount: lines.length,
  widestLine: Math.max(...lines.map((line) => [...line].length)),
  nonEmptyLines: lines.filter((line) => line.trim()).length,
  outputUnprintable: countUnprintable(rendered),
  outputBoxDrawing: (rendered.match(/[─-╿]/g) || []).length,
};

if (process.argv.includes("--json")) {
  console.log(JSON.stringify(report, null, 2));
} else {
  for (const [key, value] of Object.entries(report)) {
    console.log(`${key.padEnd(22)} ${value}`);
  }
  console.log("\nthe last six lines a reader would see:\n");
  for (const line of lines.slice(-6)) console.log(`  ${line.replace(/\s+$/, "")}`);
}

// A SCREEN WITH UNPRINTABLE CHARACTERS IN IT IS A FAILED RENDER, and saying so in the exit status is
// what lets this be run from something other than a person's eyes.
process.exit(report.blank || report.outputUnprintable > 0 ? 1 : 0);
