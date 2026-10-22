#!/bin/sh
# One more case: no /etc/claude-code file at all; the allowlist sits in a folder named by CLAUDE_CODE_MANAGED_SETTINGS_PATH
# (an undocumented variable found in the 2.1.308 binary). The fake API on :9999 must already be running.
set -u
OUT=/evidence/out-envpath
mkdir -p "$OUT" /work /tmp/aify-policy
FAKE_KEY="sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE-channelproofonly00"
rm -rf /etc/claude-code
printf '%s\n' '{"channelsEnabled": true, "allowedChannelPlugins": [{"marketplace": "aify-local", "plugin": "aify-channel"}]}' \
  > /tmp/aify-policy/managed-settings.json
export HOME=/root/envpath
mkdir -p "$HOME"
tail20=$(printf %s "$FAKE_KEY" | tail -c 20)
cat > "$HOME/.claude.json" <<JSON
{"hasCompletedOnboarding": true, "theme": "dark", "customApiKeyResponses": {"approved": ["$tail20"], "rejected": []},
 "projects": {"/work": {"hasTrustDialogAccepted": true, "hasCompletedProjectOnboarding": true}}}
JSON
B=ANTHROPIC_BASE_URL=http://127.0.0.1:9999
env ANTHROPIC_API_KEY=$FAKE_KEY $B claude plugin marketplace add /fixture/aify-local > "$OUT/install.txt" 2>&1
env ANTHROPIC_API_KEY=$FAKE_KEY $B claude plugin install aify-channel@aify-local >> "$OUT/install.txt" 2>&1
for variant in with-env without-env; do
  rm -f /tmp/aify-channel.log
  tmux kill-server 2>/dev/null
  P=""; [ "$variant" = with-env ] && P="CLAUDE_CODE_MANAGED_SETTINGS_PATH=/tmp/aify-policy"
  tmux new-session -d -s proof -x 160 -y 48 -c /work \
    "env HOME=$HOME ANTHROPIC_API_KEY=$FAKE_KEY $B $P claude --channels plugin:aify-channel@aify-local; sleep 600"
  sleep 25
  tmux capture-pane -p -t proof > "$OUT/$variant.screen-25s.txt"
  cp /tmp/aify-channel.log "$OUT/$variant.channel-server.log" 2>/dev/null || echo "(never started)" > "$OUT/$variant.channel-server.log"
  tmux kill-server 2>/dev/null
done
