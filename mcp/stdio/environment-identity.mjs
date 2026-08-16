// What this ENVIRONMENT is, and what it tells the control plane about itself.
//
// An "environment" is one host as the dashboard sees it — a machine, an OS, a set of workspace roots, and
// the runtimes it can actually launch. The environment bridge heartbeats this description; the dashboard
// draws its spawn targets from it. If the description is wrong the failure is not an error, it is a spawn
// offered for a runtime that is not installed, or a workspace root nothing can resolve.
//
// THE IDENTITY IS THE HEARTBEAT'S KEY, which is why kind/os/label live beside the payload rather than in
// some general host-info module. `environmentKind` and `environmentOs` are what an environment row is
// matched on, so two bridges that described the same host differently would register as two environments
// and split its workers between them.
//
// `cwdRootsForEnvironment` IS THE ONE WITH A REAL DECISION IN IT. It reads an explicit roots list when the
// operator set one and otherwise advertises exactly the process's own cwd. Each root is a directory the
// dashboard will let someone launch an agent in, so the list is deliberately small and ordered — the first
// one is the default workspace a spawn lands in.
//
// ONE GAP, PINNED RATHER THAN FIXED HERE: a non-empty `AIFY_CWD_ROOTS` whose every segment is blank — say
// `";;;"` — takes the explicit branch, filters to nothing, and yields `[]`. The environment then advertises
// NO place to spawn into. The default branch cannot produce that, only a malformed override can. Reported;
// changing it is a behaviour decision, and `environment-identity.test.js` asserts the current answer so a
// fix has something to flip.
//
// WHAT IT DOES NOT OWN: the timer that sends the heartbeat, or the decision to send one. This module builds
// the description; `server.js` decides when to say it. That split is what keeps it importable by a test.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import fs from "fs";
import os from "os";
import path from "path";

import { AIFY_VERSION } from "./version.js";
import { BRIDGE_BUILD_TAG } from "./bridge-build.mjs";
import { BRIDGE_INSTANCE_ID, BRIDGE_STARTED_AT } from "./bridge-instance.mjs";
import { dedupePreserveOrder } from "./dedupe.mjs";
import { advertisedEnvironmentRuntimes, advertisedTerminalRuntimes } from "./environment-runtimes.js";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { bridgeTerminalSupported } from "./terminal-runtime.js";
import { defaultMachineId } from "./runtimes.js";

// Both are aliases rather than derivations: `AIFY_VERSION` is the single release version (see CLAUDE.md,
// "one file, and a test that enforces it") and `defaultMachineId()` is a pure function of env and hostname.
// Neither can disagree with the copy `server.js` holds.
const BRIDGE_VERSION = AIFY_VERSION;
const MACHINE_ID = defaultMachineId();

export function environmentKind() {
  const explicit = String(process.env.AIFY_ENVIRONMENT_KIND || "").trim();
  if (explicit) return explicit;
  if (process.env.WSL_DISTRO_NAME) return "wsl";
  if (process.env.container || fs.existsSync("/.dockerenv")) return "docker";
  if (process.platform === "win32") return "windows";
  if (process.platform === "darwin") return "macos";
  return "linux";
}

export function environmentOs() {
  if (process.platform === "win32") return "windows";
  if (process.platform === "darwin") return "macos";
  return "linux";
}

export function environmentLabel(kind, hostname) {
  const explicit = String(process.env.AIFY_ENVIRONMENT_LABEL || "").trim();
  if (explicit) return explicit;
  if (kind === "wsl") return `WSL ${process.env.WSL_DISTRO_NAME || ""} on ${hostname}`.replace(/\s+/g, " ").trim();
  if (kind === "docker") return `Docker on ${hostname}`;
  if (kind === "windows") return `Windows on ${hostname}`;
  if (kind === "macos") return `macOS on ${hostname}`;
  return `Linux on ${hostname}`;
}

export function cwdRootsForEnvironment() {
  const explicit = String(process.env.AIFY_CWD_ROOTS || "").trim();
  if (explicit) {
    return dedupePreserveOrder(explicit.split(path.delimiter).map((item) => item.trim()).filter(Boolean));
  }
  return dedupePreserveOrder([DEFAULT_CWD]);
}

export function environmentHeartbeatPayload() {
  const hostname = (() => {
    try { return os.hostname() || "unknown-host"; } catch { return "unknown-host"; }
  })();
  const kind = environmentKind();
  const id = String(process.env.AIFY_ENVIRONMENT_ID || `${kind}:${hostname}:default`).trim();
  const terminalSupported = bridgeTerminalSupported();
  return {
    id,
    label: environmentLabel(kind, hostname),
    machineId: MACHINE_ID,
    os: environmentOs(),
    kind,
    bridgeId: BRIDGE_INSTANCE_ID,
    bridgeVersion: BRIDGE_VERSION,
    cwdRoots: cwdRootsForEnvironment(),
    runtimes: advertisedEnvironmentRuntimes(),
    terminal: terminalSupported,
    pty: terminalSupported,
    terminalRuntimes: advertisedTerminalRuntimes({ terminalSupported }),
    metadata: {
      pid: process.pid,
      platform: process.platform,
      arch: process.arch,
      node: process.version,
      cwd: DEFAULT_CWD,
      wslDistro: process.env.WSL_DISTRO_NAME || "",
      bridgeStartedAt: BRIDGE_STARTED_AT,
      // The sha of the code THIS PROCESS IS ACTUALLY RUNNING (v0.2 item B1). It was already
      // computed for the startup banner and then only written to stderr, where nothing can read
      // it — so the one fact that proves a running bridge is current was thrown away at boot.
      //
      // Why it matters here specifically: `aify-doctor`'s `bridge-running` check reads /proc and
      // SKIPS on Windows, so on this host nothing verifies that a running wrapper executes current
      // code. `bridge-installed` only proves the FILES on disk are current, which is a different
      // claim — a process keeps whatever it loaded at boot.
      //
      // That gap has a live artifact, not a hypothetical one: on 2026-08-10 I verified a
      // just-shipped multipart fix through comms_share, saw the OLD corrupted output, and nearly
      // recorded a working fix as broken. My own bridge was pre-restart and nothing said so.
      //
      // Reporting it on registration makes the check platform-independent: the server can compare
      // what each LIVE bridge is running against the checkout, with no process inspection at all.
      bridgeBuild: BRIDGE_BUILD_TAG,
    },
  };
}

// Whether a requested workspace lies inside the roots this environment advertises — the check that turns
// `cwdRootsForEnvironment`'s list from a description into a permission. It lives beside those roots because
// it is only ever called with them, and because the two must agree about what a root MEANS: the same `~`
// expansion, the same trailing-slash handling, the same treatment of "/".
export function workspaceWithinRoots(workspace, roots = []) {
  // 2026-06-03: two latent bugs made spawns into the common ['/', '~'] roots
  // (the bridge's default advertised cwdRoots) reject EVERY absolute workspace:
  //   1. The root "/" (meaning "anywhere") had its trailing slash stripped to ""
  //      and was then filter(Boolean)'d OUT, so a "/"-rooted env matched nothing.
  //   2. The root "~" was never expanded to $HOME, so an absolute workspace under
  //      the home dir never matched "~".
  // Result: managed spawns failed with "outside this bridge's advertised roots"
  // for any normal env. Fix: treat "/" as match-all, and expand "~"/"~/..".
  const home = String(process.env.HOME || process.env.USERPROFILE || "")
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
  const expand = (p) => {
    let s = String(p || "").trim().replace(/\\/g, "/");
    if (s === "~") s = home;
    else if (s.startsWith("~/")) s = `${home}/${s.slice(2)}`;
    return s.replace(/\/+$/, "");
  };
  // `..` MUST BE COLLAPSED BEFORE COMPARING. This was a pure prefix test, so
  // `/srv/repo/../../etc` starts with `/srv/repo` and passed as "inside" the root — by the check
  // that turns advertised roots "from a description into a permission". The service-side twin
  // (`_workspace_root_for`) had the identical hole, so there was no second guard behind the first.
  //
  // LEXICAL, NOT RESOLVED: no `fs.realpath`, because this runs before the cwd is used and must not
  // depend on the path existing. SYMLINKS ARE THEREFORE NOT FOLLOWED — a link inside a root that
  // points out of it still passes. That limit is stated rather than implied.
  //
  // Drive letters need no special case: `C:/Docker/..` has no leading `/`, so `C:` is an ordinary
  // first segment and pops to `C:`, which is what the comparison wants.
  const collapse = (p) => {
    const absolute = p.startsWith("/");
    const out = [];
    for (const segment of p.split("/")) {
      if (!segment || segment === ".") continue;
      if (segment === "..") {
        // Above an absolute root there is nowhere to go, which is what `/..` === `/` means. A
        // RELATIVE path keeps its leading `..` so it cannot be mistaken for something inside.
        if (out.length && out[out.length - 1] !== "..") out.pop();
        else if (!absolute) out.push("..");
        continue;
      }
      out.push(segment);
    }
    return `${absolute ? "/" : ""}${out.join("/")}`;
  };
  const normalize = (p) => collapse(expand(p)).replace(/\/+$/, "");
  const rawRoots = (roots || []).map((r) => String(r || "").trim()).filter(Boolean);
  // "/" is the match-all root.
  if (rawRoots.some((r) => r === "/")) return true;
  const value = normalize(workspace);
  const normalizedRoots = rawRoots.map(normalize).filter(Boolean);
  if (!value || !normalizedRoots.length) return true;
  return normalizedRoots.some((root) => value === root || value.startsWith(`${root}/`));
}
