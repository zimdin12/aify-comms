# Staging only: frozen runtimes, clean shallow source snapshots, no host configuration.
ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS node-runtime

FROM python:3.12.12-slim-bookworm AS dependencies
ARG CODEX_VERSION
ARG CLAUDE_VERSION
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git build-essential procps util-linux && \
    rm -rf /var/lib/apt/lists/*
# This Git configuration is image-local and contains no authentication material.
RUN git config --global url.https://github.com/.insteadOf ssh://git@github.com/ && \
    git config --global --add url.https://github.com/.insteadOf git@github.com:
RUN test -n "$CODEX_VERSION" && test -n "$CLAUDE_VERSION" && \
    npm install --global --no-audit --no-fund \
      "@openai/codex@$CODEX_VERSION" "@anthropic-ai/claude-code@$CLAUDE_VERSION"
COPY sources/baseline /src/baseline
COPY sources/candidate /src/candidate
# The context omits Git's machine-specific index and logs, not commit/tree/blob objects.
# Rebuild each index from its actual HEAD before stamping inside this disposable image.
RUN for phase in baseline candidate; do \
      for role in comms env wrapper; do git -C "/src/$phase/$role" read-tree HEAD || exit; done; \
      (cd "/src/$phase/comms" && bash scripts/stamp.sh) || exit; \
      python -m venv "/venvs/$phase" && \
      "/venvs/$phase/bin/python" -m pip install --no-cache-dir -r "/src/$phase/comms/service/requirements.txt" || exit; \
      (cd "/src/$phase/env" && npm ci --no-audit --no-fund) || exit; \
      (cd "/src/$phase/comms/mcp/stdio" && npm ci --no-audit --no-fund) || exit; \
      if [ -f "/src/$phase/wrapper/package-lock.json" ]; then \
        (cd "/src/$phase/wrapper" && npm ci --no-audit --no-fund) || exit; \
      else \
        (cd "/src/$phase/wrapper" && npm install --no-package-lock --no-audit --no-fund) || exit; \
      fi; \
    done
RUN useradd --create-home --home-dir /home/staging --shell /bin/bash staging && \
    mkdir -p /data /work && chown -R staging:staging /src /data /work
ENV HOME=/home/staging
ENV PYTHONUNBUFFERED=1
USER staging

FROM dependencies AS baseline
ENV PATH=/venvs/baseline/bin:$PATH
WORKDIR /src/baseline/comms
CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8800", "--no-access-log", "--timeout-graceful-shutdown", "3"]

FROM dependencies AS candidate
ENV PATH=/venvs/candidate/bin:$PATH
WORKDIR /src/candidate/comms
CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8800", "--no-access-log", "--timeout-graceful-shutdown", "3"]

FROM dependencies AS host
ENV NPM_CONFIG_PREFIX=/home/staging/.local
ENV PATH=/home/staging/.local/bin:$PATH
WORKDIR /work
CMD ["sleep", "infinity"]
