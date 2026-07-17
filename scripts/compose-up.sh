#!/bin/bash
# =============================================================================
# Start the service with all configured sub-services
# =============================================================================
# Reads SUB_SERVICES from .env and includes their docker-compose files.

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

# Add sub-services
if [ -n "${SUB_SERVICES}" ]; then
    IFS=',' read -ra SERVICES <<< "$SUB_SERVICES"
    for svc in "${SERVICES[@]}"; do
        svc=$(echo "$svc" | xargs)  # trim whitespace
        COMPOSE_FILE="services/${svc}/docker-compose.yml"
        if [ -f "$COMPOSE_FILE" ]; then
            COMPOSE_FILES="${COMPOSE_FILES} -f ${COMPOSE_FILE}"
            echo "Including sub-service: ${svc}"
        else
            echo "Warning: Sub-service compose file not found: ${COMPOSE_FILE}"
        fi
    done
fi

echo "Running: docker compose ${COMPOSE_FILES} up $@"
docker compose ${COMPOSE_FILES} up "$@"
