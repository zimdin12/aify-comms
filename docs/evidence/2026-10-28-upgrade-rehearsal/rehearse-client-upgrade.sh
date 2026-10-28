#!/usr/bin/env bash
# Client upgrade rehearsal on Linux: a v0.5.7 claude client install, then today's install chain, inside a throwaway
# container against a throwaway service. Nothing here touches the host's ~/.claude, ~/.aify or live service.
# The client checks out the SAME commit the service image was built from: the installer refuses bridge files that
# differ from what the service serves. The service is addressed by IP: its spec route refuses other hostnames.
set -uo pipefail
export MSYS_NO_PATHCONV=1
S="${REHEARSE_DIR:?set REHEARSE_DIR to a scratch directory}"
KEY=rehearse-key
VOL=aify-rehearse-data
NET=aify-rehearse-net
say() { printf '\n== %s\n' "$*"; }
in_c() { docker exec -u node -e HOME=/home/node -w /home/node aify-rehearse-client bash -lc "$1"; }
state() { in_c 'echo "--- ~/.claude.json mcpServers:"; node -e "const j=require(process.env.HOME+\"/.claude.json\");console.log(Object.keys(j.mcpServers||{}))" 2>&1; echo "--- settings hooks (events):"; node -e "const j=require(process.env.HOME+\"/.claude/settings.json\");console.log(Object.keys(j.hooks||{}).join(\",\"))" 2>&1; echo "--- launchers on PATH:"; for c in aify-comms claude-aify aify-env herdr-aify aify-doctor; do printf "%s -> %s\n" $c "$(command -v $c || echo none)"; done; echo "--- claude-aify transport:"; grep -m1 -oE "if \[ \"(http|stdio)\" = \"http\" \]" "$(command -v claude-aify)" 2>/dev/null || echo "no claude-aify"; echo "--- registry:"; cat ~/.aify/services.json 2>/dev/null | node -e "let s=\"\";process.stdin.on(\"data\",d=>s+=d).on(\"end\",()=>{try{const j=JSON.parse(s);for(const [k,v] of Object.entries(j.services||{}))console.log(k,\"endpoint\",v.endpoint,\"mcpTransport\",v.mcpTransport)}catch{console.log(\"none\")}})"'; }

say "clean"
docker rm -f aify-rehearse-client aify-rehearse-svc >/dev/null 2>&1; docker volume rm "$VOL" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1
docker network create "$NET" >/dev/null

say "service: today's live image on a fresh volume"
NEWIMG=$(docker inspect aify-comms-service --format '{{.Image}}')
docker run -d --name aify-rehearse-svc --network "$NET" -v "$VOL":/data -v "${AIFY_COMMS_REPO:?set AIFY_COMMS_REPO to the comms checkout}/config":/app/config:ro \
  -e API_KEY=$KEY -e DATA_DIR=/data -e SERVICE_PORT=8800 "$NEWIMG" >/dev/null
SVC_IP=$(docker inspect aify-rehearse-svc --format "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}")
SVC="http://$SVC_IP:8800"
for i in $(seq 1 60); do docker exec aify-rehearse-svc curl -sf http://127.0.0.1:8800/health >/dev/null 2>&1 && break; sleep 2; done
SHA=$(docker exec aify-rehearse-svc curl -s http://127.0.0.1:8800/version | grep -oE '"sha":"[0-9a-f]+"' | cut -d'"' -f4)
echo "service $SVC build $SHA"

say "client container (Debian, node 22, the claude CLI for its mcp config only; never logged in, never run as an agent)"
cat > "$S/client.Dockerfile" <<'EOF'
FROM node:22-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 git curl procps ca-certificates && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code@latest >/dev/null 2>&1 && chown -R node:node /usr/local/lib/node_modules /usr/local/bin
EOF
docker build -q -t aify-rehearse-client -f "$S/client.Dockerfile" "$S" > "$S/build-client.log" 2>&1 || { echo "CLIENT IMAGE BUILD FAILED"; tail -20 "$S/build-client.log"; exit 1; }
docker run -d --name aify-rehearse-client --network "$NET" aify-rehearse-client sleep infinity >/dev/null
in_c 'claude --version; node --version'

say "1. the OLD install: v0.5.7 from GitHub, claude client, as a pre-split host had it"
in_c "git clone -q https://github.com/zimdin12/aify-comms.git old && cd old && git checkout -q v0.5.7 && AIFY_API_KEY=$KEY bash install.sh --client claude $SVC > ../old-install.log 2>&1; echo old install exit \$?"
state

say "2. the UPGRADE: the service's own commit, the plain guided chain; the first pass stops for approval"
in_c "git clone -q https://github.com/zimdin12/aify-comms.git new && cd new && git checkout -q $SHA && git log -1 --format='%h %s' && AIFY_API_KEY=$KEY bash install.sh --client claude $SVC > ../new-install-1.log 2>&1; echo first pass exit \$?; grep -E 'approve|sudo|problem|chain' ../new-install-1.log | tail -8"
DIGEST=$(in_c "grep -oE -- '--approve [^ )]+' new-install-1.log | head -1 | cut -d' ' -f2")
echo "approval digest: ${DIGEST:-NONE}"
say "second pass with the printed approval"
in_c "cd new && AIFY_API_KEY=$KEY AIFY_INSTALL_APPROVE=$DIGEST bash install.sh --client claude $SVC > ../new-install-2.log 2>&1; echo second pass exit \$?; grep -E 'sudo|problem|chain|managed' ../new-install-2.log | tail -10"
say "the one sudo step the installer printed (channel policy), run as root, then the third pass"
POLICY=$(in_c "grep -E '^sudo -- ' new-install-2.log | head -1")
echo "printed: ${POLICY:-NONE}"
[ -n "$POLICY" ] && docker exec -u root aify-rehearse-client bash -lc "${POLICY#sudo -- }" && echo "policy exit 0"
in_c "cd new && AIFY_API_KEY=$KEY AIFY_INSTALL_APPROVE=$DIGEST bash install.sh --client claude $SVC > ../new-install-3.log 2>&1; echo third pass exit \$?; grep -E 'sudo|problem|chain' ../new-install-3.log | tail -6"
say "after the upgrade"
state
say "doctor (read-only)"
in_c 'env -u AIFY_AGENT_ID aify-comms doctor 2>&1 | grep -E "^  [✓✗–]|check\(s\)" | head -30'

say "teardown"
docker rm -f aify-rehearse-client aify-rehearse-svc >/dev/null 2>&1; docker volume rm "$VOL" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1
echo done
