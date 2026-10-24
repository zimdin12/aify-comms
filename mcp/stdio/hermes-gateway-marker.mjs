// Per-user gateway discovery. Unlike session/port markers, this must survive TEMP changes in MCP children.
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function defaultGatewayMarkerDir() {
  return path.join(os.homedir(), ".aify", "hermes", "gateways");
}

// IDs reaching this store are already sanitized by hermes-endpoint.js, the existing filename owner.
export class GatewayMarkerStore {
  constructor(dir = defaultGatewayMarkerDir()) { this.dir = dir; }

  markerPath(safeId) { return path.join(this.dir, `aify-hermes-gateway-${safeId}`); }

  read(safeId) {
    try {
      const parsed = JSON.parse(fs.readFileSync(this.markerPath(safeId), "utf8"));
      const gatewayUrl = String(parsed?.gatewayUrl || "").trim();
      if (!/^wss?:\/\//i.test(gatewayUrl)) return null;
      return { gatewayUrl, gatewayTokenEnv: String(parsed?.gatewayTokenEnv || "") };
    } catch { return null; }
  }

  write(safeId, gatewayUrl, gatewayTokenEnv) {
    // Publish with rename in the same directory, never a partially written JSON destination.
    const file = this.markerPath(safeId);
    const temp = `${file}.${process.pid}.${crypto.randomBytes(8).toString("hex")}.tmp`;
    try {
      // mode applies to newly created stores. Explicit legacy tempDir may be shared; never chmod it.
      fs.mkdirSync(this.dir, { recursive: true, mode: 0o700 });
      fs.writeFileSync(temp, JSON.stringify({ gatewayUrl, gatewayTokenEnv }), { flag: "wx", mode: 0o600 });
      fs.renameSync(temp, file);
      return true;
    } catch { return false; }
    finally {
      try { fs.rmSync(temp, { force: true }); } catch { /* best-effort */ }
    }
  }

  clear(safeId) {
    try { fs.rmSync(this.markerPath(safeId), { force: true }); } catch { /* best-effort teardown */ }
  }
}
