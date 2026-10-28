#!/usr/bin/env bash
# Service upgrade rehearsal: a v0.5.7 service's data, then today's image on the same volume.
# Isolated: own container names, port 18800, own volume, no host credential mounts. Prints evidence; changes nothing live.
set -uo pipefail
export MSYS_NO_PATHCONV=1
REPO="${AIFY_COMMS_REPO:?set AIFY_COMMS_REPO to the comms checkout}"
S="${REHEARSE_DIR:?set REHEARSE_DIR to a scratch directory outside the checkout}"
OLD="$S/old-0.5.7"
KEY=rehearse-key
URL=http://127.0.0.1:18800
VOL=aify-rehearse-data
H=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")

say() { printf '\n== %s\n' "$*"; }
wait_up() { for i in $(seq 1 90); do curl -sf "$URL/health" >/dev/null && return 0; sleep 2; done; echo "NOT UP"; return 1; }

say "clean any previous rehearsal"
docker rm -f aify-rehearse-old aify-rehearse-new >/dev/null 2>&1
docker volume rm "$VOL" >/dev/null 2>&1
git -C "$REPO" worktree remove --force "$OLD" >/dev/null 2>&1; rm -rf "${OLD:?}"

say "build v0.5.7"
git -C "$REPO" worktree add --detach "$OLD" v0.5.7 >/dev/null 2>&1 || { echo "worktree failed"; exit 1; }
docker build -q -t aify-rehearse-old:0.5.7 "$OLD" > "$S/build-old.log" 2>&1 || { echo "OLD BUILD FAILED"; tail -20 "$S/build-old.log"; exit 1; }

say "run v0.5.7 on :18800"
docker run -d --name aify-rehearse-old -p 18800:8800 -v "$VOL":/data -v "$OLD/config":/app/config:ro \
  -e API_KEY=$KEY -e DATA_DIR=/data -e SERVICE_PORT=8800 aify-rehearse-old:0.5.7 >/dev/null
wait_up || { docker logs aify-rehearse-old 2>&1 | tail -30; exit 1; }
curl -s "$URL/version"; echo

say "seed through the v0.5.7 API"
for a in old-lead old-coder old-hermes; do
  curl -s -o /dev/null -w "register $a %{http_code}\n" "${H[@]}" -X POST "$URL/api/v1/agents" -d "{\"agentId\":\"$a\",\"role\":\"coder\",\"name\":\"$a\",\"description\":\"seeded by the upgrade rehearsal\"}"
done
for i in 1 2 3; do
  curl -s -o /dev/null -w "send $i %{http_code}\n" "${H[@]}" -X POST "$URL/api/v1/messages/send" -d "{\"from_agent\":\"old-lead\",\"to\":\"old-coder\",\"type\":\"info\",\"subject\":\"seed $i\",\"body\":\"body $i\"}"
done
curl -s -o /dev/null -w "channel %{http_code}\n" "${H[@]}" -X POST "$URL/api/v1/channels" -d '{"name":"rehearse","description":"seed","createdBy":"old-lead"}'
echo -n "CONTROL on v0.5.7: "; curl -s "${H[@]}" "$URL/api/v1/agents" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);const m=j.agents||j;const ids=Array.isArray(m)?m.map(x=>x.id||x.agentId):Object.keys(m);console.log("seeded agents seen:",ids.filter(i=>String(i).startsWith("old-")).join(",")||"NONE","(of",ids.length,"total)")})'
curl -s "${H[@]}" "$URL/api/v1/channels" | head -c 200; echo
docker exec aify-rehearse-old python -c "
import sqlite3,glob;p=sorted(glob.glob('/data/*.db'));print('db files',p)
c=sqlite3.connect(p[0]);print({t:c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('agents','messages')})"

say "stop v0.5.7, start today's image on the same volume"
docker stop aify-rehearse-old >/dev/null
NEWIMG=$(docker inspect aify-comms-service --format '{{.Image}}')
docker run -d --name aify-rehearse-new -p 18800:8800 -v "$VOL":/data -v "$REPO/config":/app/config:ro \
  -e API_KEY=$KEY -e DATA_DIR=/data -e SERVICE_PORT=8800 "$NEWIMG" >/dev/null
wait_up || { echo "NEW SERVICE DID NOT COME UP ON OLD DATA"; docker logs aify-rehearse-new 2>&1 | tail -40; exit 1; }
curl -s "$URL/version"; echo

say "read back on today's service"
curl -s "${H[@]}" "$URL/api/v1/agents" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);const m=j.agents||j;const ids=Array.isArray(m)?m.map(x=>x.id||x.agentId):Object.keys(m);console.log("seeded agents seen:",ids.filter(i=>String(i).startsWith("old-")).join(",")||"NONE","(of",ids.length,"total)")})'
curl -s "${H[@]}" "$URL/api/v1/messages/inbox/old-coder?filter=all&limit=10" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const j=JSON.parse(s);const m=j.messages||j;console.log("old-coder messages:",m.length,m.map(x=>x.subject).join(","))}catch{console.log("inbox read:",s.slice(0,200))}})'
curl -s "${H[@]}" "$URL/api/v1/channels" | head -c 300; echo
say "errors in today's service log"
docker logs aify-rehearse-new 2>&1 | grep -iE "error|traceback|migrat" | head -20
echo "(end of errors)"

say "teardown"
docker rm -f aify-rehearse-old aify-rehearse-new >/dev/null 2>&1
docker volume rm "$VOL" >/dev/null 2>&1
docker image rm aify-rehearse-old:0.5.7 >/dev/null 2>&1
git -C "$REPO" worktree remove --force "$OLD" >/dev/null 2>&1; git -C "$REPO" worktree prune
echo done
