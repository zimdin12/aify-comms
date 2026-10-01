// Does a native program started through `<name>.cmd` -> Git Bash -> the program see a console?
//
// The question behind keeping a handwritten PowerShell hermes launcher (review of P6r, H1). hermes'
// TUI falls back to the classic CLI, or the Ink TUI bails out, when stdin or stdout is not a TTY
// (hermes 0.21.5 hermes_cli/main_tui_launch.py `_resolve_use_tui`), and the skill's troubleshooting
// note says a `hermes-tui: no TTY` exit meant the `.cmd` was not calling the PowerShell launcher.
//
// Run inside a pseudo-console (node-pty, the ConPTY a terminal window gives cmd.exe), three ways:
//   direct   cmd.exe /c node probe            positive control: a console, so TTY
//   shim     cmd.exe /c probe.cmd             the claude/codex shim shape: bash.exe runs the probe
//   piped    cmd.exe /c piped.cmd              negative control: `echo x | node probe`, stdin a pipe
//
//   node cmd-to-bash-keeps-a-console.mjs <path to node-pty> <bash.exe>
// Prints one JSON line per case and writes them to the -result.json beside this file.
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const [ptyPath, bashExe] = process.argv.slice(2);
const pty = createRequire(import.meta.url)(ptyPath);
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-tty-"));
const probe = path.join(dir, "probe.js");
fs.writeFileSync(probe, 'process.stdout.write("TTY=" + JSON.stringify({ stdin: !!process.stdin.isTTY, stdout: !!process.stdout.isTTY }) + "\\n");\n');
const node = process.execPath;
// The shim, written the way install.sh's install_windows_cmd_shim writes one: CRLF, %~dp0, bash.exe on PATH.
fs.writeFileSync(path.join(dir, "probe-sh"), `#!/bin/bash\nexec "${node.replaceAll("\\", "/")}" "${probe.replaceAll("\\", "/")}"\n`);
const bashDir = path.dirname(bashExe);
fs.writeFileSync(path.join(dir, "probe.cmd"), [
  "@echo off", "setlocal",
  `set "PATH=${bashDir};${bashDir}\\..\\usr\\bin;${bashDir}\\..\\..\\bin;%PATH%"`,
  `"${bashExe}" "%~dp0probe-sh" %*`, "endlocal", ""].join("\r\n"));
fs.writeFileSync(path.join(dir, "piped.cmd"), ["@echo off", `echo x | "${node}" "${probe}"`, ""].join("\r\n"));

function run(label, args) {
  return new Promise((resolve) => {
    let out = "";
    const term = pty.spawn("cmd.exe", args, { cols: 120, rows: 30, cwd: dir, env: process.env, useConpty: true });
    term.onData((d) => { out += d; });
    term.onExit(({ exitCode }) => {
      const seen = out.match(/TTY=(\{[^}]*\})/);
      resolve({ case: label, exitCode, tty: seen ? JSON.parse(seen[1]) : null });
    });
  });
}

const results = [
  await run("direct", ["/c", node, probe]),
  await run("shim", ["/c", path.join(dir, "probe.cmd")]),
  await run("piped", ["/c", path.join(dir, "piped.cmd")]),
];
for (const r of results) console.log(JSON.stringify(r));
fs.writeFileSync(fileURLToPath(new URL("./cmd-to-bash-keeps-a-console-result.json", import.meta.url)),
  `${JSON.stringify({ node: process.version, bash: bashExe, results }, null, 2)}\n`);
fs.rmSync(dir, { recursive: true, force: true });
// node-pty keeps handles open after its children exit.
process.exit(0);
