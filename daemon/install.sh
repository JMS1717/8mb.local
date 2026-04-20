#!/bin/bash
# Install 8mb.local hardware acceleration daemon for macOS

set -e

echo "=== 8mb.local macOS Hardware Daemon Installer ==="

# 0. Source Homebrew on Apple Silicon if not in PATH
if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# 1. Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "Error: Homebrew is required but not installed."
    echo "Please install from https://brew.sh/"
    exit 1
fi

# 2. Check/Install FFmpeg (with videotoolbox support)
if ! command -v ffmpeg &> /dev/null; then
    echo "Installing FFmpeg via Homebrew..."
    brew install ffmpeg
else
    echo "FFmpeg is already installed."
fi

# 3. Setup Python venv in a TCC-safe location
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
INSTALL_DIR="$HOME/.8mb.local-daemon"

echo "Installing daemon to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$DIR/8mb-daemon.py" "$INSTALL_DIR/"
cp "$DIR/requirements.txt" "$INSTALL_DIR/"

if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "Installing requirements..."
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# 4. Configure Port
read -p "Enter port for daemon to run on [default: 7998]: " INPUT_PORT
DAEMON_PORT=${INPUT_PORT:-7998}

# 5. Create LaunchAgent plist
PLIST_PATH="$HOME/Library/LaunchAgents/com.8mb.local.daemon.plist"
echo "Creating LaunchAgent at $PLIST_PATH (using port $DAEMON_PORT)..."

cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.8mb.local.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/venv/bin/python3</string>
        <string>$INSTALL_DIR/8mb-daemon.py</string>
    </array>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DAEMON_PORT</key>
        <string>$DAEMON_PORT</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/8mb.local-daemon.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/8mb.local-daemon.err.log</string>
</dict>
</plist>
EOF

# Load LaunchAgent
echo "Loading LaunchAgent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl bootout gui/$(id -u) "$PLIST_PATH" 2>/dev/null || true

# Prioritize the reliable deprecated load for headless SSH, fallback to bootstrap
if ! launchctl bootstrap gui/$(id -u) "$PLIST_PATH" 2>/dev/null; then
    launchctl load -w "$PLIST_PATH"
fi

echo "=== Installation complete ==="
echo "The daemon is now running on port $DAEMON_PORT."
echo "You can check status with: curl http://localhost:$DAEMON_PORT/health"
