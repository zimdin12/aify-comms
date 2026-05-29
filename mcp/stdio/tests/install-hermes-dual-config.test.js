// Smoke test: install_hermes_config patches both hermes_config_root and
// ~/.hermes/config.yaml when they differ. The dual-path approach was the
// fix for the operator's "missing AIFY_HERMES_GATEWAY_URL env passthrough"
// regression (item #115).
import assert from "assert";
import test from "node:test";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INSTALL_SH = path.resolve(__dirname, "../../../install.sh");

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

test("install.sh distinguishes installer paths from Windows Hermes runtime paths", () => {
  const src = fs.readFileSync(INSTALL_SH, "utf8");
  assert.match(
    src,
    /path_for_windows_runtime/,
    "expected a helper for paths embedded into native Windows Hermes config/runtime env"
  );
  assert.match(
    src,
    /wslpath -w/,
    "expected WSL installs to convert embedded Hermes runtime paths with wslpath -w"
  );
  assert.match(
    src,
    /node_config_file="\$\(path_for_node "\$config_file"\)"/,
    "installer Node process should still read/write the config via its local path"
  );
  // The server.js path stored in the hermes MCP config must only be converted
  // to a Windows drive path when hermes actually runs as a native Windows
  // binary. On WSL with a Linux hermes, an unconditional path_for_windows_runtime
  // emitted "D:\..." which Linux node cannot open, so the aify-comms MCP child
  // exited instantly and managed/resident delivery never worked. The conversion
  // is therefore gated on hermes_runtime_is_native_windows.
  assert.match(
    src,
    /hermes_runtime_is_native_windows/,
    "hermes config path conversion must be gated on native-Windows detection so Linux hermes on WSL keeps the Linux path"
  );
  assert.match(
    src,
    /node_server_path="\$\(path_for_windows_runtime "\$node_server_path"\)"/,
    "native Windows hermes still converts the stored server.js path via path_for_windows_runtime"
  );
});
