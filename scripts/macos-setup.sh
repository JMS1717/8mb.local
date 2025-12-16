#!/bin/bash
#
# macOS Setup Script for 8mb.local Native Worker
#
# This script sets up a native macOS worker that uses VideoToolbox
# for hardware-accelerated video encoding on Apple Silicon (M1/M2/M3/M4).
#
# Architecture:
#   - Docker: Runs Redis, frontend, and backend API
#   - Native: Runs Celery worker with VideoToolbox access
#

set -e

echo "=== 8mb.local macOS Worker Setup ==="
echo ""

# Check for Homebrew (needed if we need to install missing deps)
HAS_BREW=false
if command -v brew &> /dev/null; then
    HAS_BREW=true
fi

# Check for Apple Silicon
ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    echo "Warning: This script is optimized for Apple Silicon (arm64)."
    echo "Detected architecture: $ARCH"
    echo ""
fi

# Check for Python 3.10+
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
        version=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_CMD="$cmd"
            echo "Found Python $version"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "Python 3.10+ not found."
    if [ "$HAS_BREW" = true ]; then
        echo "Installing Python via Homebrew..."
        brew install python
        PYTHON_CMD="python3"
    else
        echo "Please install Python 3.10 or newer, or install Homebrew first."
        exit 1
    fi
fi

# Check for FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "FFmpeg not found."
    if [ "$HAS_BREW" = true ]; then
        echo "Installing FFmpeg via Homebrew..."
        brew install ffmpeg
    else
        echo "Please install FFmpeg, or install Homebrew first."
        exit 1
    fi
fi

# Verify FFmpeg has VideoToolbox support
if ! ffmpeg -encoders 2>/dev/null | grep -q "h264_videotoolbox"; then
    echo "Error: FFmpeg does not have VideoToolbox support."
    if [ "$HAS_BREW" = true ]; then
        echo "Try reinstalling: brew reinstall ffmpeg"
    fi
    exit 1
fi
echo "FFmpeg VideoToolbox support: OK"

# Setup Python virtual environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "Setting up Python virtual environment..."
cd "$PROJECT_DIR"

if [ ! -d "venv" ]; then
    "$PYTHON_CMD" -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To run the worker:"
echo ""
echo "  1. Start Docker containers:"
echo "     docker-compose -f docker-compose.macos.yml up -d"
echo ""
echo "  2. In a new terminal, start the native worker:"
echo "     cd $PROJECT_DIR"
echo "     source venv/bin/activate"
echo "     export REDIS_URL=redis://localhost:6380/0"
echo "     export UPLOAD_DIR=$PROJECT_DIR/uploads"
echo "     export OUTPUT_DIR=$PROJECT_DIR/outputs"
echo "     export PYTHONPATH=$PROJECT_DIR:$PROJECT_DIR/backend-api:$PROJECT_DIR/worker"
echo "     celery -A worker.celery_app worker --loglevel=info"
echo ""
echo "  3. Open http://localhost:8001 in your browser"
echo ""
