// Inventory uses the launcher's parser; missing dependencies/errors stay unknown.
import fs from "node:fs";
import { parseRegistry } from "../mcp/stdio/node_modules/aify-wrapper/lib/registry.mjs";
import { SERVICE_NAME } from "../mcp/stdio/service-name.mjs";
try {
  const result = parseRegistry(fs.readFileSync(process.argv[2], "utf8"));
  console.log(result.ok ? (Object.hasOwn(result.registry.services, SERVICE_NAME) ? "yes" : "no") : "unknown");
} catch {
  console.log("unknown");
}
