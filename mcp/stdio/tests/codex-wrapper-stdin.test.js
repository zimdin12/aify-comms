import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const installScript = readFileSync(resolve(__dirname, "../../../install.sh"), "utf8");

assert.match(
  installScript,
  /setsid codex app-server --listen "\$APP_SERVER_URL" <\/dev\/null >>"\$LOG_FILE" 2>&1 &/,
  "codex-aify must not leave the background app-server attached to terminal stdin",
);

assert.match(
  installScript,
  /exec codex --remote "\$APP_SERVER_URL" "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" "\$\{CODEX_ARGS\[@\]\}"/,
  "codex-aify must replace the wrapper with the foreground Codex TUI",
);

console.log("codex-wrapper-stdin.test.js: all assertions passed");
