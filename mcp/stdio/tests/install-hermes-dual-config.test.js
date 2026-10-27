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
  // The hermes MCP entry stored a server.js path until 0.9.11 (now the service's /mcp/http). For the wrapper's
  // AIFY_HERMES_PLUGIN_PATH / PYTHONPATH: an unconditional path_for_windows_runtime
  // baked a "D:\..." plugin path that Linux python silently ignored, so the aify
  // Hermes plugin never loaded and the TUI died with "gateway exited". The plugin
  // path conversion must be gated on the same native-Windows predicate so Linux
  // hermes on WSL keeps the Linux PYTHONPATH.
  assert.match(
    src,
    /hermes_plugin_path="\$\(path_for_windows_runtime "\$hermes_plugin_path"\)"/,
    "native Windows hermes converts the plugin PYTHONPATH via path_for_windows_runtime"
  );
  // Anchored on the assignment of the copy beside the bridge (install_hermes_plugin writes it, 0.9.10).
  const pluginGate = src.split(/hermes_plugin_path="\$AIFY_NATIVE_BASE\/integrations\/hermes-aify-plugin"/)[1] || "";
  assert.ok(pluginGate, "control: the plugin path assignment the gate follows was found");
  assert.match(
    pluginGate.split(/\n\n/)[0] || "",
    /hermes_runtime_is_native_windows/,
    "plugin PYTHONPATH conversion must be gated on native-Windows detection so Linux hermes on WSL keeps the Linux path"
  );
});
