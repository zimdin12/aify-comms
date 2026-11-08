#!/usr/bin/env bash
# Runs only inside the isolated staging host. Sources and dependencies were prepared in its image.
set -euo pipefail
source_root="${1:?source tuple directory required}"
for role in comms env wrapper; do
  [ -d "$source_root/$role" ] || { printf 'missing staged source role: %s\n' "$role" >&2; exit 64; }
done
export PATH="$HOME/.local/bin:$PATH"
export NPM_CONFIG_OFFLINE=true NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false
: "${AIFY_API_KEY:?synthetic staging API key required}"
# Cold bootstrap installs the credential owner first. This script starts no daemon.
bash "$source_root/env/install.sh" --no-prompt
bash "$source_root/comms/install.sh" --standalone --client codex http://service:8800 --with-hook
# Select the tuple's actual wrapper helpers as well as its templates. A template-only override
# would leave WRAPPER_DIR pointing at the older dependency beneath the bridge.
bash "$source_root/wrapper/install.sh" --client codex --endpoint http://service:8800 \
  --registry "$HOME/.aify/services.json" --bridge-dir "$HOME/.aify-comms/mcp/stdio" \
  --native-base "$HOME/.aify-comms" --wrapper-dir "$source_root/wrapper" \
  --host-repo "$source_root/comms"
for role in comms env wrapper; do
  printf 'source %s ' "$role"
  git -C "$source_root/$role" rev-parse HEAD
done
