#!/bin/bash
# =============================================================================
# Migrate aify-comms v1 (JSON files) to v2 (SQLite)
#
# Prerequisites:
#   - v1 container stopped (docker compose down)
#   - Python 3.12 with aiosqlite installed
#   - Run from the aify-comms repo root
# =============================================================================
set -e

echo "=== aify-comms v1 -> v2 migration ==="
echo ""

# Check we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    echo "ERROR: Run this from the aify-comms repo root"
    exit 1
fi

# Step 1: Export v1 data from Docker volume
echo "Step 1: Exporting v1 data from Docker volume..."
docker run --rm \
    -v service-data:/data:ro \
    -v "$(pwd)":/app \
    -w /app \
    python:3.12-slim \
    python -c "
import json, sys
sys.path.insert(0, '.')
from service.export_v1 import export_v1
from pathlib import Path
bundle = export_v1(Path('/data'))
Path('v1-export.json').write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding='utf-8')
print(f'Exported: {len(bundle[\"agents\"])} agents, {len(bundle[\"messages\"])} messages, {len(bundle[\"channels\"])} channels, {len(bundle[\"shared\"])} artifacts')
"

if [ ! -f "v1-export.json" ]; then
    echo "ERROR: Export failed — v1-export.json not created"
    exit 1
fi

echo "Export saved to v1-export.json"
echo ""

# Step 2: Import into v2 SQLite
echo "Step 2: Importing into v2 SQLite..."
pip install aiosqlite --quiet 2>/dev/null

python -c "
import asyncio, json
from pathlib import Path
from service.import_v2 import import_v2
bundle = json.loads(Path('v1-export.json').read_text(encoding='utf-8'))
asyncio.run(import_v2(bundle, Path('data/aify.db')))
print('Import complete')
"

if [ ! -f "data/aify.db" ]; then
    echo "ERROR: Import failed — data/aify.db not created"
    exit 1
fi

echo "Database created at data/aify.db"
echo ""

# Step 3: Rebuild and start v2
echo "Step 3: Rebuilding container with v2 code..."
docker compose up -d --build

echo ""
echo "=== Migration complete ==="
echo "Verify:    curl http://localhost:8800/health"
echo "Dashboard: http://localhost:8800"
echo ""
echo "The v1-export.json file is kept as a backup."
