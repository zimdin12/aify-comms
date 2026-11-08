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
# The synthetic client has a readable, initially untrusted project map, not a provider login.
node --input-type=module - <<'NODE'
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const file = path.join(os.homedir(), '.claude.json');
const config = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {};
if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('invalid fixture Claude config');
config.projects ??= {};
if (typeof config.projects !== 'object' || Array.isArray(config.projects)) throw new Error('invalid fixture project map');
fs.writeFileSync(file, JSON.stringify(config) + String.fromCharCode(10), { mode: 0o600 });
NODE
# Cold bootstrap installs the credential owner first. This script starts no daemon.
bash "$source_root/env/install.sh" --no-prompt
bash "$source_root/comms/install.sh" --standalone --client claude http://service:8800
bash "$source_root/comms/install.sh" --standalone --client codex http://service:8800 --with-hook
# Select the tuple's actual helpers and templates for both clients used at the joint switch.
for client in claude codex; do
  bash "$source_root/wrapper/install.sh" --client "$client" --endpoint http://service:8800 \
    --registry "$HOME/.aify/services.json" --bridge-dir "$HOME/.aify-comms/mcp/stdio" \
    --native-base "$HOME/.aify-comms" --wrapper-dir "$source_root/wrapper" \
    --host-repo "$source_root/comms"
done
for role in comms env wrapper; do
  printf 'source %s ' "$role"
  git -C "$source_root/$role" rev-parse HEAD
done
