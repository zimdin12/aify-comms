FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 (needed for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user
ARG DOCKER_GID=999
RUN groupadd -g ${DOCKER_GID} docker 2>/dev/null || true && \
    useradd -m -s /bin/bash service && \
    usermod -aG docker service && \
    mkdir -p /app /data /home/service/.claude && \
    chown -R service:service /app /data /home/service/.claude

WORKDIR /app

# Install Python dependencies
COPY service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install MCP client dependencies
COPY mcp/stdio/package.json mcp/stdio/package-lock.json ./mcp/stdio/
RUN cd mcp/stdio && npm ci

# Copy service code
COPY service/ ./service/
COPY mcp/ ./mcp/
COPY config/ ./config/
COPY .agents/ ./.agents/
COPY install.sh ./install.sh

VOLUME /data

# Mount point for Claude auth — mount ~/.claude from host
# docker-compose.yml maps this automatically
VOLUME /home/service/.claude

EXPOSE 8800 8801

USER service

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8800/health || exit 1

CMD ["python", "-m", "uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8800"]
