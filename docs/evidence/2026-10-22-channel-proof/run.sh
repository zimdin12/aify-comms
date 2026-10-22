#!/bin/sh
# Runs inside a throwaway node container. Three cases, each with a fresh HOME:
#   allowlisted    managed allowedChannelPlugins names the plugin; --channels plugin:aify-channel@aify-local
#   not-listed     managed settings without the plugin in allowedChannelPlugins; the same --channels (negative control)
#   dev-flag       --dangerously-load-development-channels plugin:aify-channel@aify-local (positive control: the warning)
# The API key is a fake string: it reaches no account. Screens are tmux captures.
set -u
OUT=${OUT:-/evidence/out}
EXTRA=${EXTRA:-}
mkdir -p "$OUT" /work
FAKE_KEY="sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE-channelproofonly00"
claude --version > "$OUT/claude-version.txt" 2>&1

run_case() {
  name=$1; managed=$2; flags=$3
  export HOME="/root/$name"
  mkdir -p "$HOME"
  tail20=$(printf %s "$FAKE_KEY" | tail -c 20)
  cat > "$HOME/.claude.json" <<JSON
{"hasCompletedOnboarding": true, "theme": "dark", "customApiKeyResponses": {"approved": ["$tail20"], "rejected": []},
 "projects": {"/work": {"hasTrustDialogAccepted": true, "hasCompletedProjectOnboarding": true}}}
JSON
  mkdir -p /etc/claude-code
  printf '%s\n' "$managed" > /etc/claude-code/managed-settings.json
  cp /etc/claude-code/managed-settings.json "$OUT/$name.managed-settings.json"
  env ANTHROPIC_API_KEY=$FAKE_KEY $EXTRA claude plugin marketplace add /fixture/aify-local > "$OUT/$name.install.txt" 2>&1
  env ANTHROPIC_API_KEY=$FAKE_KEY $EXTRA claude plugin install aify-channel@aify-local >> "$OUT/$name.install.txt" 2>&1
  rm -f /tmp/aify-channel.log
  tmux kill-server 2>/dev/null
  tmux new-session -d -s proof -x 160 -y 48 -c /work \
    "env HOME=$HOME ANTHROPIC_API_KEY=$FAKE_KEY $EXTRA claude $flags; sleep 600"
  for t in 8 20 35; do
    sleep $(( t - ${prev:-0} )); prev=$t
    tmux capture-pane -p -t proof > "$OUT/$name.screen-${t}s.txt"
  done
  prev=0
  cp /tmp/aify-channel.log "$OUT/$name.channel-server.log" 2>/dev/null || echo "(the channel server never started)" > "$OUT/$name.channel-server.log"
  tmux kill-server 2>/dev/null
}

ALLOW='{"channelsEnabled": true, "allowedChannelPlugins": [{"marketplace": "aify-local", "plugin": "aify-channel"}]}'
NOLIST='{"channelsEnabled": true, "allowedChannelPlugins": [{"marketplace": "aify-local", "plugin": "some-other-plugin"}]}'
run_case allowlisted "$ALLOW" "--channels plugin:aify-channel@aify-local"
run_case not-listed "$NOLIST" "--channels plugin:aify-channel@aify-local"
run_case dev-flag "$NOLIST" "--dangerously-load-development-channels plugin:aify-channel@aify-local"
echo done > "$OUT/finished.txt"
