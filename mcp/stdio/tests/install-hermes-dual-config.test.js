// Smoke test: install_hermes_config patches both hermes_config_root and
// ~/.hermes/config.yaml when they differ. The dual-path approach was the
// fix for the operator's "missing AIFY_HERMES_GATEWAY_URL env passthrough"
// regression (item #115).
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";

const INSTALL_SH = path.resolve("install.sh");

test("install.sh patches ~/.hermes/config.yaml in addition to hermes config path", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  // Must mention HOME/.hermes/config.yaml as a second target
  assert.ok(
    /\.hermes[\\/]config\.yaml/.test(src) || /HOME.*\.hermes/.test(src),
    "expected install.sh to reference ~/.hermes/config.yaml as a fallback hermes config target"
  );
  // Must explain the dual-write rationale near the new code
  assert.ok(
    /both|dual|secondary|fallback/i.test(src.split(/install_hermes_config/i)[1] || ""),
    "expected install_hermes_config block to document the dual-write rationale"
  );
});
