# aify-env from a `git archive` of one commit, in a home of its own. Built by env-against-comms.mjs; never published.
FROM node:24-slim
WORKDIR /opt/aify-env
COPY . .
# node-pty and the other optional packages may fail to build here; npm skips an optional package that fails, and
# agent state needs none of them.
RUN npm ci --no-audit --no-fund
ENV HOME=/home/sandbox
RUN mkdir -p /home/sandbox/.aify && chmod +x /opt/aify-env/sandbox-entry.sh
# A stub claude launcher, so a managed agent can be defined (aify-env refuses a harness with no launcher on PATH). It
# carries the marker the scan reads and runs nothing: started, it exits 1.
RUN printf '#!/bin/sh\nHARNESS_WRAPPER_VERSION="sandbox-stub"\necho "sandbox stub launcher: runs nothing" >&2\nexit 1\n' \
    > /usr/local/bin/claude-aify && chmod +x /usr/local/bin/claude-aify
ENTRYPOINT ["/opt/aify-env/sandbox-entry.sh"]
