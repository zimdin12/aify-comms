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
      const comspec = process.env.ComSpec || process.env.COMSPEC || "cmd.exe";
      const result = spawnSync(comspec, ["/d", "/s", "/c", `where ${value}`], {
        windowsHide: true,
        timeout: 3000,
        encoding: "utf-8",
      });
      attempts.push({ method: "where", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      if (result.status === 0) {
        const lines = String(result.stdout || "").split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        if (lines.length) resolved = lines[0];
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
  RESOLVED_EXECUTABLE_CACHE.set(value, resolved);
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
