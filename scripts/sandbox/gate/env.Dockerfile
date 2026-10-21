# The gate's aify-env: a `git archive` of one commit, as ../env.Dockerfile builds it, with two differences.
#  - Build tools, so node-pty builds. Without a terminal comms never asks this host to start a worker, and the gate's
#    stand-in is started the way a real Claude worker is: as `claude-aify`, by aify-env, for a lifetime of its own.
#  - The stand-in launcher (gate/claude-aify.sh, gate/stand-in.mjs) in place of the stub that exits 1.
#  - The relay (gate/relay.mjs), which a pair runs from this same image as a container of its own.
# Built by gate/gate.mjs; never published.
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/aify-env
COPY . .
RUN npm ci --no-audit --no-fund && node -e "require('node-pty')"
ENV HOME=/home/sandbox
RUN mkdir -p /home/sandbox/.aify /opt/gate && chmod +x /opt/aify-env/sandbox-entry.sh \
 && mv /opt/aify-env/gate-stand-in.mjs /opt/gate/stand-in.mjs && mv /opt/aify-env/gate-relay.mjs /opt/gate/relay.mjs \
 && mv /opt/aify-env/gate-claude-aify.sh /usr/local/bin/claude-aify && chmod +x /usr/local/bin/claude-aify
ENTRYPOINT ["/opt/aify-env/sandbox-entry.sh"]
