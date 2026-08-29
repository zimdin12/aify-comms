// install.sh writes launcher bodies with unquoted heredocs. A backtick in one of them runs a command.
//
// FOUND 2026-08-29, live, in the launcher on the operator's own machine. The rendered environment
// bridge said:
//
//     # collision this tier exists to end.  reports both the setting and whether
//
// A sentence with its subject missing. The source says `'aify-comms doctor' reports both...` and the
// backticks around it were command substitution inside `cat > "$wrapper_path" <<EOF`. So every
// install.sh run that wrote that launcher EXECUTED `aify-comms doctor`, a second pair in the usage
// text executed `aify-doctor`, and both had their stdout spliced into the file.
//
// HARMLESS BY LUCK, WHICH IS NOT A PROPERTY WORTH KEEPING. Both are read-only verifiers. A bare
// `aify-comms` in that prose would have STARTED AN ENVIRONMENT BRIDGE during an install -- superseding
// the one already serving the host and reaping its managed workers. That is the 2026-08-20 incident
// verbatim, where a backtick inside an unquoted heredoc reaped seven managed gateway hosts, re-armed
// in a file nobody thinks of as executable prose.
//
// It also cost real time. Two doctor runs per render, on a path the suites render dozens of times:
// one `--emit-wrappers` render went from 8366/9561/8574ms to 4300/4034/4083ms on this host.
//
// THE RULE IS NO UNESCAPED BACKTICK, and it is narrower than it sounds. `$(...)` and `$VAR` in an
// unquoted heredoc stay legal and executable -- that is how a launcher gets its endpoint baked in.
// This does not prove a heredoc runs nothing; it proves markdown punctuation cannot become a command.
//
// TWO ARMS, BECAUSE THE TEMPLATES ARE SOMEBODY ELSE'S SOURCE. aify-wrapper owns `wrappers/*.in` and
// gets the authoritative gate. What this repo owns is the question a consumer must ask anyway: are the
// PINNED BYTES we render, and the launchers we render FROM them, free of it. Rendering can also
// produce shell text that was never literally in a template, which is why the rendered arm exists and
// is not redundant with upstream's.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

// ONE SCANNER, TWO ROLES. aify-wrapper owns the templates, so it owns the rule and ships the scanner
// in `lib/`. This repo imports it rather than keeping a second copy: two hand-written scanners of one
// question agree until one of them is fixed, which is the failure that retired four doctor checks
// here. What stays local is the POPULATION -- this repo's own shell producers, the bytes its pin
// resolved to, and what it renders from them.
import { scanHeredocs } from "aify-wrapper/lib/heredoc-scan.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE = path.resolve(HERE, "..");
const REPO = path.resolve(BRIDGE, "..", "..");
const BACKTICK = String.fromCharCode(96);
const BACKSLASH = String.fromCharCode(92);
const TAB = String.fromCharCode(9);
const NL = String.fromCharCode(10);

const REMEDY = "a backtick inside an unquoted heredoc is a command that runs while the file is being "
  + "WRITTEN, not when it is run. Use straight quotes in prose. If the substitution is deliberate, "
  + "write it as $(...) so it reads as one.";

function clean(name, source) {
  const { backticks, unterminated } = scanHeredocs(source);
  assert.deepEqual(unterminated, [], `${name}: a heredoc never ends, so the scan lost the file's `
    + "structure and everything it reported after that point is guesswork");
  assert.deepEqual(backticks, [], `${name}: ${REMEDY}\n${JSON.stringify(backticks, null, 2)}`);
}

// ---- this repo's own shell producers -------------------------------------------------------------

test("THE GATE: no unquoted heredoc in this repo's shell producers contains a backtick", () => {
  // The three scripts that WRITE files a shell later executes. A gate guarding one of three producers
  // reports green exactly like one guarding all three, which is how the 1000-line gate read
  // `service/**` only for months.
  for (const name of ["install.sh", "redeploy.sh", "setup.sh"]) {
    const file = path.join(REPO, name);
    if (!existsSync(file)) continue;
    clean(name, readFileSync(file, "utf8"));
  }
});

// ---- the pinned package we consume, and what we render from it ------------------------------------

test("THE CONSUMER ARM: the PINNED aify-wrapper templates are clean", () => {
  // Upstream's gate is authoritative for its own tree. This asks the only question a consumer can
  // answer for itself: are the bytes THIS pin resolved to free of it. A clean upstream HEAD says
  // nothing about the sha in our lockfile.
  //
  // DERIVED FROM THE DIRECTORY. A fifth template is picked up the day it lands; a hand-listed four
  // would have gone on reporting green about a file it never opened.
  const dir = path.join(BRIDGE, "node_modules", "aify-wrapper", "wrappers");
  if (!existsSync(dir)) return; // no install yet; the rendered arm below still runs
  const templates = readdirSync(dir).filter((name) => name.endsWith(".in"));
  assert.ok(templates.length >= 4, `only ${templates.length} templates found; the scan would prove `
    + "little about a package whose wrappers directory it cannot read");
  for (const name of templates) clean(`aify-wrapper/wrappers/${name}`, readFileSync(path.join(dir, name), "utf8"));
});

test("THE RENDERED ARM: no launcher this installer writes contains one either", () => {
  // NOT redundant with the template arm. Substitution puts values INTO the body, so a rendered
  // launcher can carry shell text that was never literally in a template -- and it is the rendered
  // file that a human runs. `--emit-wrappers` exits before npm, MCP registration, hook install or any
  // environment mutation, so this cannot touch a live host's bin.
  const dir = mkdtempSync(path.join(tmpdir(), "aify-heredoc-"));
  try {
    execFileSync("bash", [path.join(REPO, "install.sh"), "--client", "claude", "http://127.0.0.2:1",
      "--emit-wrappers", dir], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
    const files = readdirSync(dir).filter((name) => !name.endsWith(".cmd") && !name.endsWith(".ps1"));
    assert.ok(files.length, "--emit-wrappers produced nothing");
    for (const name of files) clean(`rendered/${name}`, readFileSync(path.join(dir, name), "utf8"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// ---- controls ------------------------------------------------------------------------------------

test("POSITIVE CONTROL: the scanner finds the exact shape that shipped", () => {
  const shipped = [
    'cat > "$wrapper_path" <<EOF',
    "# a silent fallback would mean two spawners on one host -- the exact",
    `# collision this tier exists to end. ${BACKTICK}aify-comms doctor${BACKTICK} reports both the setting`,
    'export AIFY_COMMS_DELEGATE_SPAWNS="$DELEGATE_SPAWNS"',
    "EOF",
  ].join(NL);
  const { backticks } = scanHeredocs(shipped);
  assert.equal(backticks.length, 1);
  assert.equal(backticks[0].line, 3);
  assert.equal(backticks[0].delimiter, "EOF");
});

test("MUTANT: an indented terminator is BODY, and the scan must not stop there", () => {
  // THE REVIEWER'S MUTANT, and it took the first version of this scanner apart. It compared each line
  // with `.trim()`, which is not shell semantics: for a plain `<<EOF` the delimiter must match the
  // whole line. Executed against that version, this returned `[]` -- the live backticks below the
  // fake terminator read as ordinary script and the file scanned clean.
  const mutant = ["cat <<EOF", "  EOF", `${BACKTICK}printf danger${BACKTICK}`, "EOF"].join(NL);
  const { backticks } = scanHeredocs(mutant);
  assert.equal(backticks.length, 1, "a space-indented EOF ended the heredoc, so the backticks below "
    + "it were never scanned");
  assert.equal(backticks[0].line, 3);
});

test("MUTANT: a TAB-indented terminator is body for `<<`, and the terminator for `<<-`", () => {
  // The dash is the whole difference, and only TABS are stripped.
  const plain = ["cat <<EOF", `${TAB}EOF`, `${BACKTICK}printf danger${BACKTICK}`, "EOF"].join(NL);
  assert.equal(scanHeredocs(plain).backticks.length, 1, "`<<` strips nothing");

  const dashed = ["cat <<-EOF", `${TAB}harmless`, `${TAB}EOF`, `${BACKTICK}outside${BACKTICK}`].join(NL);
  assert.deepEqual(scanHeredocs(dashed).backticks, [], "`<<-` strips leading tabs, so this heredoc "
    + "ended before the backticks and they are ordinary script");

  const spaced = ["cat <<-EOF", "  EOF", `${BACKTICK}printf danger${BACKTICK}`, "EOF"].join(NL);
  assert.equal(scanHeredocs(spaced).backticks.length, 1, "`<<-` strips TABS, never spaces");
});

test("an unterminated heredoc is a typed failure, not a clean scan", () => {
  // Reaching end-of-file without the delimiter means the walk lost the file's structure. Reporting
  // zero findings there is the false green the rest of this file is about.
  const { unterminated } = scanHeredocs(["cat <<EOF", `${BACKTICK}printf danger${BACKTICK}`].join(NL));
  assert.deepEqual(unterminated, [{ line: 1, delimiter: "EOF" }]);
});

test("NEGATIVE CONTROL: a quoted heredoc is not an offence", () => {
  // `<<'EOF'` writes its body verbatim. Flagging one would make every embedded node and python program
  // in install.sh unwritable, and the gate would be deleted rather than obeyed.
  const quoted = ["cat <<'EOF'", `# see ${BACKTICK}aify-comms doctor${BACKTICK}`, "EOF"].join(NL);
  assert.deepEqual(scanHeredocs(quoted).backticks, []);
});

test("NEGATIVE CONTROL: an escaped backtick and a deliberate $(...) both pass", () => {
  // pi's template relies on the first: escaped backticks inside an unquoted heredoc, intended as
  // literal output. The second is the form this rule steers people toward, so flagging it would leave
  // no legal way to substitute.
  const body = [
    "cat <<EOF",
    `echo "run ${BACKSLASH}${BACKTICK}aify-comms doctor${BACKSLASH}${BACKTICK} next" >&2`,
    'node $(shell_quote "$script")',
    "EOF",
  ].join(NL);
  assert.deepEqual(scanHeredocs(body).backticks, []);
});

test("the scan sees past the first heredoc in a file", () => {
  // The walk has to resume AFTER each terminator. A version that stopped at the first would have
  // reported install.sh clean on a 3000-line file with four of them.
  const two = ["cat <<'A'", "nothing here", "A",
    "cat <<B", `# ${BACKTICK}danger${BACKTICK}`, "B"].join(NL);
  assert.equal(scanHeredocs(two).backticks.length, 1);
});
