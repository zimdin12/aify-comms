#!/bin/bash
# =============================================================================
# aify-comms - Initial Setup
# =============================================================================
set -e

echo "aify-comms Setup"
echo "================"

# Copy config files if they don't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from template"
else
    echo ".env already exists, skipping"
fi

if [ ! -f config/service.json ]; then
    cp config/service.example.json config/service.json
    echo "Created config/service.json from template"
else
    echo "config/service.json already exists, skipping"
fi

if [ ! -f docker-compose.override.yml ]; then
    cp docker-compose.override.yml.example docker-compose.override.yml
    echo "Created docker-compose.override.yml from template"
else
    echo "docker-compose.override.yml already exists, skipping"
fi

# Initialize git submodules if any
if [ -f .gitmodules ]; then
    git submodule update --init --recursive
    echo "Git submodules initialized"
fi

# Make scripts executable
chmod +x scripts/*.sh 2>/dev/null || true

echo ""
echo "Setup complete! Next steps:"
echo "  1. Edit .env to configure your service"
echo "  2. Edit config/service.json for service-specific settings"
echo "  3. Run: docker compose up -d --build"
echo "  4. Test: ./scripts/test-endpoints.sh"
