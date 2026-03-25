#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "=========================================="
echo "🚔 Zone 1 Crime Intelligence System Setup"
echo "=========================================="

# Check if Node.js is installed
if ! command -v node >/dev/null 2>&1; then
    echo "[ERROR] Node.js is required but not installed."
    echo "Please install Node.js (v18 or higher) before running this script."
    exit 1
fi

# Check if npm is installed
if ! command -v npm >/dev/null 2>&1; then
    echo "[ERROR] npm is required but not installed."
    echo "Please install npm before running this script."
    exit 1
fi

echo "[INFO] Node.js and npm are installed. Proceeding with setup..."

# Ensure we are in the correct directory (the project root where setup.sh should be)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Navigate to backend to install dependencies
if [ -d "backend" ]; then
    echo "[INFO] Navigating to backend directory..."
    cd backend
else
    echo "[ERROR] 'backend' directory not found. Are you running this script from the project root?"
    exit 1
fi

echo "[INFO] Installing Node.js dependencies..."
npm install

echo "=========================================="
echo "[SUCCESS] Setup complete! Starting the service..."
echo "=========================================="

# Start the Node.js server
npm start
