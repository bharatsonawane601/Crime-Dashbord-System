#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "=========================================="
echo "  Zone 1 Crime Intelligence System Setup"
echo "  Python FastAPI Backend"
echo "=========================================="

# Check if Python3 is installed
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] Python 3 is required but not installed."
    exit 1
fi

echo "[INFO] Python3 found: $(python3 --version)"

# Install system dependencies (Debian/Ubuntu)
if command -v apt-get >/dev/null 2>&1; then
    echo "[INFO] Installing system dependencies..."
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-dev build-essential pkg-config 2>/dev/null || true
fi

# Ensure we are in the correct directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Navigate to backend
if [ -d "backend" ]; then
    echo "[INFO] Navigating to backend directory..."
    cd backend
else
    echo "[ERROR] 'backend' directory not found."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[INFO] Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "[INFO] Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "[INFO] Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt

echo "=========================================="
echo "[SUCCESS] Setup complete! Starting the service..."
echo "=========================================="

# Start the FastAPI server with Uvicorn
python main.py
