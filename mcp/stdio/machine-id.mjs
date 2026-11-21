import os from "node:os";
import { machineIdFor } from "aify-env/client/machine-id";
import { hostIsWsl } from "aify-env/host-wsl";

export { hostIsWsl };

export function defaultMachineId() {
  let hostname = "";
  try { hostname = os.hostname(); } catch { /* the owner handles missing host evidence */ }
  return machineIdFor({ platform: process.platform, hostname, env: process.env, isWsl: hostIsWsl() });
}
