FROM python:3.12-slim

# Both service and dashboard healthchecks use curl.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash service && \
    mkdir -p /app /data /keys /home/service/.claude && \
    chown -R service:service /app /data /keys /home/service/.claude && \
    chmod 700 /keys

WORKDIR /app

# Install Python dependencies
COPY service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The host stamp writes the build identity, registration spec and bridge manifest into service/.
COPY service/ ./service/
COPY mcp/sse_server.py ./mcp/sse_server.py
COPY config/ ./config/
COPY integrations/ ./integrations/
COPY .agents/ ./.agents/
COPY install.sh ./install.sh
COPY scripts/ ./scripts/

VOLUME /data

# Mount point for Claude auth — mount ~/.claude from host
# docker-compose.yml maps this automatically
VOLUME /home/service/.claude

EXPOSE 8800 8801

USER service

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8800/health || exit 1

# --timeout-graceful-shutdown: uvicorn waits for open connections before the lifespan shutdown, and a
# long-poll holds one for 20-600 s, so without a bound Docker killed the process before the terminal
# output drain ran. docker-compose.yml's stop_grace_period covers this wait plus the drain.
CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8800", "--no-access-log", "--timeout-graceful-shutdown", "3"]
