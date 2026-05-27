import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const installScript = readFileSync(resolve(__dirname, "../../../install.sh"), "utf8");

assert.match(
  installScript,
  /setsid codex "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" app-server --listen "\$APP_SERVER_URL" <\/dev\/null >>"\$LOG_FILE" 2>&1 &/,
  "codex-aify must not leave the background app-server attached to terminal stdin",
);

assert.match(
  installScript,
  /run_codex_foreground --remote "\$APP_SERVER_URL" "\$\{CODEX_PERMISSION_FLAGS\[@\]\}" "\$\{CODEX_ARGS\[@\]\}"/,
  "codex-aify must run the foreground Codex TUI through the wrapper helper",
);

assert.match(
  installScript,
  /codex "\$@"/,
  "codex-aify must keep the visible Codex TUI in the foreground so stdin stays a terminal",
);

assert.doesNotMatch(
  installScript,
  /codex "\$@" &/,
  "codex-aify must not background the visible Codex TUI; bash detaches stdin for async jobs",
);

assert.match(
  installScript,
  /CODEX_AUTO=true/,
  "codex-aify should default to unattended Codex permission mode",
);

assert.match(
  installScript,
  /CODEX_PERMISSION_FLAGS\+=\(--dangerously-bypass-approvals-and-sandbox\)/,
  "codex-aify should use Codex's supported unattended bypass flag",
);

assert.match(
  installScript,
  /--safe\|--no-auto\|--no-dangerous-permissions/,
  "codex-aify should expose an explicit safe-mode opt-out",
);

console.log("codex-wrapper-stdin.test.js: all assertions passed");
