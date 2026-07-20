// runtimes-exec.js — executable resolution, shebang inspection, and launch
// diagnostics shared by the per-runtime default-command helpers. Extracted
// verbatim from runtimes.js (task #123). runtimes.js re-exports the public
// surface.
import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

export function bashShebangFallback(absPath) {
  // Wrap the script in `bash -lic 'exec "$0" "$@"'` so the shell sources
  // both .profile (login: -l) AND .bashrc (interactive: -i) before exec'ing
  // the script. Nvm's installer adds its init to .bashrc by default — a
  // plain `bash -l` would miss it and the broken-shebang problem would
  // recur. Using exec preserves stdin/stdout/stderr semantics for Node.
  return {
    command: "bash",
    args: ["-lic", `exec "$0" "$@"`, absPath],
  };
}

const RESOLVED_EXECUTABLE_CACHE = new Map();
const EXECUTABLE_RESOLUTION_LOG = new Map();

function isReallyExecutable(absPath) {
  if (!absPath || !/[\\/]/.test(absPath)) return false;
  try {
    const st = fs.statSync(absPath);
    if (!st.isFile()) return false;
    if (process.platform !== "win32") {
      // Check exec bit for the current user
      fs.accessSync(absPath, fs.constants.X_OK);
    }
    return true;
  } catch {
    return false;
  }
}

// Walks the bridge process's own PATH (the one the kernel will use when
// invoking /usr/bin/env <name>) and returns the absolute path to the first
// executable match. Does NOT spawn a shell — must match kernel semantics.
function findOnProcessPath(name) {
  if (!name || /[\\/]/.test(name)) {
    return isReallyExecutable(name) ? name : null;
  }
  const PATH = String(process.env.PATH || "");
  const sep = process.platform === "win32" ? ";" : ":";
  const exts = process.platform === "win32"
    ? String(process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean)
    : [""];
  for (const dir of PATH.split(sep)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext);
      if (isReallyExecutable(candidate)) return candidate;
    }
  }
  return null;
}

// Splits PATH into candidate directories. cmd.exe tolerates quoted entries
// ("C:\Program Files\x"); strip the quotes so path.join doesn't embed them.
function windowsPathDirs(pathString) {
  return String(pathString || "")
    .split(";")
    .map((dir) => dir.trim().replace(/^"(.*)"$/, "$1"))
    .filter(Boolean);
}

function windowsPathExts(pathExtString) {
  const raw = String(pathExtString || "").trim() || ".COM;.EXE;.BAT;.CMD";
  return raw.split(";").map((ext) => ext.trim()).filter(Boolean);
}

// In-process Windows PATH resolution. `where` is NOT consulted here: its
// stdout arrives in the console's OEM codepage, so any non-ASCII character in
// a path (a profile like C:\Users\KertMõttus) is lossily transcoded before
// Node can read it — the "path" it prints may not exist on disk (õ -> o).
// An fs walk sees real Unicode filenames and cannot mangle.
//
// Semantics follow cmd.exe: first PATH directory containing a match wins;
// within a directory PATHEXT order decides. A bare extension-less file (the
// Git-Bash wrapper script that sits next to its .cmd shim) is only spawnable
// through a POSIX shell, so it is the LAST resort within each directory —
// this also fixes ASCII-only hosts, where `where` listed the bash script
// first and the old code blindly took line one. The current directory is
// deliberately NOT searched (unlike `where`): resolving a runtime wrapper
// from whatever cwd the bridge happens to run in would let any repo checkout
// plant a claude-aify.cmd and have the bridge execute it.
export function resolveOnWindowsPath(name, options = {}) {
  const value = String(name || "").trim();
  if (!value || /[\\/]/.test(value)) return null;
  const {
    pathString = process.env.PATH,
    pathExtString = process.env.PATHEXT,
    isExecutable = isReallyExecutable,
  } = options;
  const exts = windowsPathExts(pathExtString);
  const lower = value.toLowerCase();
  const hasWinExt = exts.some((ext) => lower.endsWith(ext.toLowerCase()));
  for (const dir of windowsPathDirs(pathString)) {
    const base = path.join(dir, value);
    // PATHEXT entries are conventionally UPPERCASE while files on disk are
    // conventionally lowercase; NTFS doesn't care but returning the on-disk
    // casing keeps results deterministic (and lets the test suite assert the
    // same behavior on case-sensitive CI filesystems). Lowercase first.
    const candidates = [];
    const push = (candidate) => { if (!candidates.includes(candidate)) candidates.push(candidate); };
    if (hasWinExt) {
      push(base);
    } else {
      for (const ext of exts) {
        push(base + ext.toLowerCase());
        push(base + ext);
      }
      push(base);
    }
    for (const candidate of candidates) {
      if (isExecutable(candidate)) return candidate;
    }
  }
  return null;
}

// Inspects a script's #! line. Returns { interpreter, args, valid, missing }
// or null if the file is not a script. valid=false means we can prove the
// interpreter is unreachable from THIS PROCESS's PATH (which is what the
// kernel will use for /usr/bin/env <name>); missing carries the offending
// interpreter name so error messages can be specific.
export function inspectShebang(absPath) {
  if (process.platform === "win32") return null;
  try {
    const fd = fs.openSync(absPath, "r");
    try {
      const buf = Buffer.alloc(512);
      const bytes = fs.readSync(fd, buf, 0, 512, 0);
      const text = buf.slice(0, bytes).toString("utf-8");
      if (!text.startsWith("#!")) return null;
      const firstLine = text.split(/\r?\n/, 1)[0].slice(2).trim();
      if (!firstLine) return null;
      const tokens = firstLine.split(/\s+/);
      const interpreter = tokens.shift();
      const args = tokens;
      let valid = false;
      let missing = null;
      if (interpreter === "/usr/bin/env" || interpreter === "/bin/env") {
        if (!fs.existsSync(interpreter)) {
          missing = interpreter;
        } else if (args.length === 0) {
          valid = true;
        } else {
          const target = args[0];
          // CRITICAL: validate against process.env.PATH (kernel-level
          // semantics), NOT against an interactive shell. A `sh -lc command
          // -v node` may succeed because the shell sources .bashrc/.profile,
          // but the kernel's execve of /usr/bin/env will only see the
          // bridge process's PATH. These two routinely disagree for
          // nvm/asdf/fnm setups.
          if (/[\\/]/.test(target)) {
            valid = isReallyExecutable(target);
            if (!valid) missing = target;
          } else {
            const onProc = findOnProcessPath(target);
            valid = onProc !== null;
            if (!valid) missing = target;
          }
        }
      } else {
        valid = isReallyExecutable(interpreter);
        if (!valid) missing = interpreter;
      }
      return { interpreter, args, valid, missing, line: firstLine };
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

export function resolveExecutable(command) {
  const value = String(command || "").trim();
  if (!value) return null;
  if (/[\\/]/.test(value)) {
    return isReallyExecutable(value) ? value : null;
  }
  if (RESOLVED_EXECUTABLE_CACHE.has(value)) {
    return RESOLVED_EXECUTABLE_CACHE.get(value);
  }
  let resolved = null;
  const attempts = [];
  try {
    if (process.platform === "win32") {
      // Primary: in-process PATH+PATHEXT walk — codepage-proof (see
      // resolveOnWindowsPath) and already fs-verified.
      resolved = resolveOnWindowsPath(value);
      attempts.push({
        method: "path-walk",
        status: resolved ? 0 : 1,
        stdout: resolved || `no PATH/PATHEXT match for "${value}"`,
      });
      if (!resolved) {
        // Last-resort probe for exotic setups. Every line `where` prints may
        // be OEM-mangled (õ -> o), so a line is only a HINT: accept it iff it
        // exists on disk, preferring real Windows executables (PATHEXT) over
        // extension-less scripts Node cannot spawn directly.
        const comspec = process.env.ComSpec || process.env.COMSPEC || "cmd.exe";
        const result = spawnSync(comspec, ["/d", "/s", "/c", `where ${value}`], {
          windowsHide: true,
          timeout: 3000,
          encoding: "utf-8",
        });
        attempts.push({ method: "where", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
        if (result.status === 0) {
          const lines = String(result.stdout || "").split(/\r?\n/).map(s => s.trim()).filter(Boolean);
          const existing = lines.filter((line) => isReallyExecutable(line));
          const exts = windowsPathExts(process.env.PATHEXT).map((ext) => ext.toLowerCase());
          resolved = existing.find((line) => exts.some((ext) => line.toLowerCase().endsWith(ext))) || existing[0] || null;
        }
      }
    } else {
      const quoted = value.replace(/'/g, "'\\''");
      // Try login shell first (sources .profile so npm-global etc. resolve)
      let result = spawnSync("sh", ["-lc", `command -v '${quoted}' 2>/dev/null`], {
        timeout: 3000,
        encoding: "utf-8",
      });
      attempts.push({ method: "sh -lc command -v", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      if (result.status !== 0 || !String(result.stdout || "").trim()) {
        // Non-login fallback uses the current process's PATH directly
        result = spawnSync("sh", ["-c", `command -v '${quoted}' 2>/dev/null`], {
          timeout: 3000,
          encoding: "utf-8",
        });
        attempts.push({ method: "sh -c command -v", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      }
      // Last-ditch: an interactive bash that sources .bashrc (nvm puts its
      // shim init in .bashrc, not .profile, so login-shell sh doesn't see it)
      if (result.status !== 0 || !String(result.stdout || "").trim()) {
        result = spawnSync("bash", ["-ic", `command -v '${quoted}' 2>/dev/null`], {
          timeout: 3000,
          encoding: "utf-8",
          env: process.env,
        });
        attempts.push({ method: "bash -ic command -v", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      }
      const out = String(result.stdout || "").trim();
      if (result.status === 0 && out) resolved = out;
    }
  } catch (err) {
    attempts.push({ method: "exception", error: err?.message || String(err) });
  }
  // Verify the resolved path is something Node can actually spawn. A common
  // failure mode: `command -v claude` returns "claude" (a shell function) or
  // a path that exists but lacks the exec bit for the current user.
  if (resolved && !isReallyExecutable(resolved)) {
    attempts.push({ method: "stat-check", rejected: resolved, reason: "not a real executable file" });
    resolved = null;
  }
  // Second failure mode: the file exists with the exec bit but its shebang
  // line points at an interpreter the kernel can't reach (e.g., a stale
  // /home/.../node path from an uninstalled nvm version, or `#!/usr/bin/env
  // node` on a system where node isn't on the bridge's PATH). execve will
  // return ENOENT against the SCRIPT, not the interpreter, which is what
  // produces the confusing "spawn /home/.../claude ENOENT" message.
  if (resolved) {
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      attempts.push({
        method: "shebang-check",
        rejected: resolved,
        reason: `shebang interpreter "${shebang.missing}" is not reachable from this bridge (shebang: #!${shebang.line})`,
      });
      // Don't null out resolved — the user may want to set
      // AIFY_CLAUDE_COMMAND to a different wrapper. But surface the problem
      // in the resolution log so runtimeLaunchAvailability can report it.
    }
  }
  // Only POSITIVE-cache a successful resolution. Caching a `null` permanently
  // (bughunt 2026-07-03) meant a transient probe timeout — or a runtime installed
  // AFTER the bridge started — pinned the runtime as unlaunchable for the whole
  // bridge lifetime, falling back to a bare-name spawn that can't see the login
  // PATH until restart. A miss re-probes next call (probes are cheap); the
  // resolution LOG still records every attempt for describeExecutableResolution.
  if (resolved) RESOLVED_EXECUTABLE_CACHE.set(value, resolved);
  EXECUTABLE_RESOLUTION_LOG.set(value, { resolved, attempts });
  return resolved;
}

export function describeExecutableResolution(command) {
  const value = String(command || "").trim();
  if (!value) return { resolved: null, attempts: [] };
  if (!EXECUTABLE_RESOLUTION_LOG.has(value)) resolveExecutable(value);
  return EXECUTABLE_RESOLUTION_LOG.get(value) || { resolved: null, attempts: [] };
}

export function hasExecutable(command) {
  return resolveExecutable(command) !== null;
}

function pathSummary() {
  const value = String(process.env.PATH || "").trim();
  if (!value) return "(empty)";
  const parts = value.split(path.delimiter).filter(Boolean);
  return parts.length > 6 ? `${parts.length} entries; head: ${parts.slice(0, 6).join(path.delimiter)} ...` : value;
}

// Read the build tag the same way server.js does, so error messages stamp
// the running bridge's git SHA. This lets users prove which code emitted
// the error without grep'ing the source.
function readBuildTag() {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const gitDir = path.resolve(here, "..", "..", ".git");
    const headPath = path.join(gitDir, "HEAD");
    if (!fs.existsSync(headPath)) return "no-git";
    const head = fs.readFileSync(headPath, "utf-8").trim();
    if (head.startsWith("ref:")) {
      const refPath = path.join(gitDir, head.slice(4).trim());
      if (fs.existsSync(refPath)) return fs.readFileSync(refPath, "utf-8").trim().slice(0, 12);
      const packed = path.join(gitDir, "packed-refs");
      if (fs.existsSync(packed)) {
        const refName = head.slice(4).trim();
        for (const line of fs.readFileSync(packed, "utf-8").split(/\r?\n/)) {
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
const BRIDGE_BUILD_TAG = readBuildTag();

export function diagnosticsFor(name) {
  const info = describeExecutableResolution(name);
  const tried = (info.attempts || []).map(a => {
    const tag = a.method;
    if (a.rejected) return `[rejected ${a.rejected}: ${a.reason}]`;
    if (a.error) return `[${tag}: error ${a.error}]`;
    return `[${tag}: status=${a.status}${a.stdout ? ` stdout="${a.stdout}"` : ""}]`;
  }).join(" ");
  return `bridge build=${BRIDGE_BUILD_TAG} pid=${process.pid} script=${fileURLToPath(import.meta.url)}; attempts: ${tried || "(none)"}; bridge PATH: ${pathSummary()}`;
}
