#!/bin/bash
set -e

echo "==========================================="
echo "  Initializing Docker Setup (Linux/Mac)"
echo "==========================================="

# 1. Ensure Files Exist (to prevent Docker creating directories)
if [ ! -f .env ]; then
    echo "Creating empty .env..."
    touch .env
fi

if [ ! -f withings_tokens.pkl ]; then
    echo "Creating placeholder withings_tokens.pkl..."
    touch withings_tokens.pkl
fi

# 2. Build Container
echo ""
echo "Building Docker container..."
# Try docker-compose, fall back to "docker compose" (v2) if first fails
if command -v docker-compose &> /dev/null; then
    docker-compose build
else
    docker compose build
fi

# 3. Run Setup Script in Container
echo ""
echo "Launching interactive setup..."
if command -v docker-compose &> /dev/null; then
    docker-compose run -it --rm garmin-sync python setup.py
else
    docker compose run -it --rm garmin-sync python setup.py
fi

if [ $? -ne 0 ]; then
    echo ""
    echo "[ERROR] Setup script failed. See above for details."
    exit 1
fi

echo ""
echo "==========================================="
echo "  Setup Finished"
echo "==========================================="
echo "To start the server, run: docker-compose up -d"
echo ""
