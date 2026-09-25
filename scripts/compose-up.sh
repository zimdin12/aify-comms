#!/bin/bash
# =============================================================================
# Start the service, with docker-compose.override.yml when one exists
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Containers do not receive .git, so stamp the source tree before any build. Without this the
# running /version endpoint can truthfully serve an older commit after a successful rebuild.
if [[ " $* " == *" --build "* ]]; then
    bash scripts/stamp.sh
fi

# Load .env if exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^$' | xargs)
fi

# Build compose file list
COMPOSE_FILES="-f docker-compose.yml"

# Add override if exists
if [ -f docker-compose.override.yml ]; then
    COMPOSE_FILES="${COMPOSE_FILES} -f docker-compose.override.yml"
fi

echo "Running: docker compose ${COMPOSE_FILES} up $@"
docker compose ${COMPOSE_FILES} up "$@"
