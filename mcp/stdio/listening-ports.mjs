// Which process is listening on which TCP port -- the one fact about a process that does not depend on
// being allowed to read its command line.
//
// THE INCIDENT, 2026-09-15. After every agent was stopped, a hermes gateway still listened on 127.0.0.1:9273,
// mc-senior-dev's port. It had been relaunched by `hermes update` run from an Administrator terminal, so it
// was ELEVATED, and to this non-elevated host its command line and executable path both read as empty.
// `gateway-orphans` finds gateways by the `--port` on a command line, so it reported "no hermes gateway host
// is running in the managed port range" in the same minute netstat showed the socket; kill-prior matches
// the same way and could neither see it nor stop it. The socket table names the owning pid regardless.
//
// STRICT: a listing that failed throws. Every caller here decides that a port is FREE from what it does not
// find, and an empty answer from a failed query would make that decision for it.

import { spawnSync as nodeSpawnSync } from "node:child_process";
import process from "node:process";

/** The port at the end of `127.0.0.1:9273`, `0.0.0.0:9273`, `[::1]:9273` or `*:9273`, or null. */
function portOf(address) {
  const match = /:(\d+)$/.exec(String(address || ""));
  const port = match ? Number(match[1]) : NaN;
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : null;
}

/** `netstat -ano -p TCP` (Windows): `  TCP    127.0.0.1:9273    0.0.0.0:0    LISTENING    65916`. */
export function parseNetstatListeners(text) {
  const found = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const match = /^\s*TCP\s+(\S+)\s+\S+\s+LISTENING\s+(\d+)\s*$/i.exec(line);
    const port = match ? portOf(match[1]) : null;
    if (port) found.push({ port, pid: Number(match[2]) });
  }
  return found;
}

/** `ss -ltnpH` (Linux): the local address is the fourth column; the pid is absent for a socket this user may not inspect. */
export function parseSsListeners(text) {
  const found = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const fields = line.trim().split(/\s+/);
    if (fields[0] !== "LISTEN" || fields.length < 4) continue;
    const port = portOf(fields[3]);
    const pid = /pid=(\d+)/.exec(line);
    if (port) found.push({ port, pid: pid ? Number(pid[1]) : null });
  }
  return found;
}

/** `lsof -nP -iTCP -sTCP:LISTEN -F pn` (macOS): a `p<pid>` line, then one `n<address>` line per socket. */
export function parseLsofListeners(text) {
  const found = [];
  let pid = null;
  for (const line of String(text || "").split(/\r?\n/)) {
    if (line.startsWith("p")) pid = Number(line.slice(1)) || null;
    const port = line.startsWith("n") ? portOf(line.slice(1)) : null;
    if (port) found.push({ port, pid });
  }
  return found;
}

const PROBES = Object.freeze({
  win32: { command: "netstat", args: ["-ano", "-p", "TCP"], parse: parseNetstatListeners },
  linux: { command: "ss", args: ["-ltnpH"], parse: parseSsListeners },
  darwin: { command: "lsof", args: ["-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pn"], parse: parseLsofListeners },
});

/**
 * Every listening TCP socket on this host as `{port, pid}`, one entry per port and pid. THROWS when the
 * listing could not be taken, including on a platform with no probe.
 */
export function listListeners({ platform = process.platform, run = nodeSpawnSync } = {}) {
  const probe = PROBES[platform];
  if (!probe) throw new Error(`no listening-socket probe for ${platform}`);
  const res = run(probe.command, probe.args, { encoding: "utf8", windowsHide: true, timeout: 10_000, maxBuffer: 16 * 1024 * 1024 });
  if (!res || res.error || res.status !== 0) {
    throw new Error(`${probe.command} failed: ${res?.error?.code || res?.error?.message || `status ${res?.status}`}`);
  }
  const unique = new Map();
  for (const entry of probe.parse(res.stdout)) unique.set(`${entry.port}:${entry.pid}`, entry);
  return [...unique.values()];
}
