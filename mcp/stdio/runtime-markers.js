#!/usr/bin/env node

import fs from "fs";
import os from "os";
import path from "path";
import { createHash } from "crypto";
import { fileURLToPath } from "url";

function normalizeRuntime(runtime) {
  const value = String(runtime || "").trim().toLowerCase();
  if (value === "claude" || value === "claude-code") return "claude-code";
  if (value === "codex") return "codex";
  if (value === "opencode") return "opencode";
  return value || "generic";
}

function stateRoot() {
  const xdgState = String(process.env.XDG_STATE_HOME || "").trim();
  if (xdgState) return xdgState;
  return path.join(os.homedir(), ".local", "state");
}

function markerBaseDir() {
  return path.join(stateRoot(), "aify-comms", "runtime-markers");
}

function markerHash(cwd) {
  return createHash("sha256").update(String(cwd || "").trim()).digest("hex");
}

export function markerFilePath(runtime, cwd) {
  const normalizedRuntime = normalizeRuntime(runtime);
  const resolvedCwd = String(cwd || "").trim() || process.cwd();
  return path.join(markerBaseDir(), `${normalizedRuntime}-${markerHash(resolvedCwd)}.json`);
}

export function isProcessAlive(pid) {
  const numericPid = Number(pid || 0);
  if (!Number.isInteger(numericPid) || numericPid <= 0) return false;
  try {
    process.kill(numericPid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

export function readRuntimeMarker(runtime, cwd) {
  const file = markerFilePath(runtime, cwd);
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
    if (!parsed || typeof parsed !== "object") return null;
    if (!isProcessAlive(parsed.pid)) {
      try {
        fs.unlinkSync(file);
      } catch {
        // best effort
      }
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function writeRuntimeMarker(runtime, cwd, data = {}) {
  const file = markerFilePath(runtime, cwd);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const payload = {
    runtime: normalizeRuntime(runtime),
    cwd: String(cwd || "").trim() || process.cwd(),
    pid: process.pid,
    createdAt: new Date().toISOString(),
    ...data,
  };
  fs.writeFileSync(file, JSON.stringify(payload, null, 2) + "\n");
  return file;
}

export function removeRuntimeMarker(runtime, cwd) {
  const file = markerFilePath(runtime, cwd);
  try {
    fs.unlinkSync(file);
  } catch {
    // best effort
  }
  return file;
}

function cliUsage() {
  console.error("Usage: runtime-markers.js <write|remove|path|read> <runtime> <cwd> [json]");
}

const THIS_FILE = fileURLToPath(import.meta.url);

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(THIS_FILE)) {
  const [, , command, runtimeArg, cwdArg, jsonArg] = process.argv;
  if (!command || !runtimeArg || !cwdArg) {
    cliUsage();
    process.exit(1);
  }

  try {
    if (command === "write") {
      const data = jsonArg ? JSON.parse(jsonArg) : {};
      const file = writeRuntimeMarker(runtimeArg, cwdArg, data);
      process.stdout.write(`${file}\n`);
    } else if (command === "remove") {
      const file = removeRuntimeMarker(runtimeArg, cwdArg);
      process.stdout.write(`${file}\n`);
    } else if (command === "path") {
      process.stdout.write(`${markerFilePath(runtimeArg, cwdArg)}\n`);
    } else if (command === "read") {
      const data = readRuntimeMarker(runtimeArg, cwdArg);
      process.stdout.write(`${JSON.stringify(data || null)}\n`);
    } else {
      cliUsage();
      process.exit(1);
    }
  } catch (error) {
    console.error(error?.message || String(error));
    process.exit(1);
  }
}
