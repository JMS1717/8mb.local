#!/bin/bash
# Uninstall 8mb.local hardware acceleration daemon for macOS

echo "=== 8mb.local macOS Hardware Daemon Uninstaller ==="

INSTALL_DIR="$HOME/.8mb.local-daemon"
PLIST_PATH="$HOME/Library/LaunchAgents/com.8mb.local.daemon.plist"
LOG_OUT="/tmp/8mb.local-daemon.out.log"
LOG_ERR="/tmp/8mb.local-daemon.err.log"

echo "Attempting to stop and unload LaunchAgent..."
launchctl bootout gui/$(id -u) "$PLIST_PATH" 2>/dev/null || echo "Daemon was not running."

if [ -f "$PLIST_PATH" ]; then
    echo "Removing LaunchAgent plist ($PLIST_PATH)..."
    rm -f "$PLIST_PATH"
fi

if [ -d "$INSTALL_DIR" ]; then
    echo "Removing uninstallation directory and Python virtual environment ($INSTALL_DIR)..."
    rm -rf "$INSTALL_DIR"
fi

echo "Removing orphaned logs..."
rm -f "$LOG_OUT" "$LOG_ERR"

echo "=== Uninstallation complete ==="
echo ""
read -p "Would you also like to immediately remove FFmpeg and its Homebrew dependencies? (y/N): " REMOVE_FFMPEG

if [[ "$REMOVE_FFMPEG" =~ ^[Yy]$ ]]; then
    echo "Uninstalling FFmpeg and unused dependencies..."
    
    # 0. Source Homebrew on Apple Silicon if not in PATH
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    
    if command -v brew &> /dev/null; then
        brew uninstall ffmpeg
        brew autoremove
        echo "FFmpeg and dependencies removed."
    else
        echo "Error: Homebrew command not found. Cannot remove FFmpeg automatically."
    fi
else
    echo "FFmpeg and other Homebrew dependencies were left untouched."
fi
