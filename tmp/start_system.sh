#!/bin/bash
# Quant Trading System Launcher
# Called by start_ui_mac.app

QUANT_DIR="/Users/bytedance/Desktop/vk/quant/.vibe-kanban-workspaces/cf89-update-the-ui/quant"
PYTHON_PATH="/usr/bin/python3"
NPM_PATH="/Users/bytedance/.nvm/versions/node/v22.22.0/bin/npm"
LOG_DIR="/tmp"

# Check and start API server
if ! lsof -i :5000 2>/dev/null | grep -q LISTEN; then
    cd "$QUANT_DIR"
    nohup "$PYTHON_PATH" api_server.py > "$LOG_DIR/api_server.log" 2>&1 &
    echo "API server starting..."
    sleep 3
fi

# Wait for API
for i in {1..10}; do
    if lsof -i :5000 2>/dev/null | grep -q LISTEN; then
        break
    fi
    sleep 1
done

# Check and start frontend
if ! lsof -i :3000 2>/dev/null | grep -q LISTEN; then
    cd "$QUANT_DIR/frontend"
    if [ ! -d "node_modules" ]; then
        echo "Installing npm packages..."
        "$NPM_PATH" install > "$LOG_DIR/npm_install.log" 2>&1
    fi
    nohup env BROWSER=none "$NPM_PATH" start > "$LOG_DIR/frontend.log" 2>&1 &
    echo "Frontend starting..."
    sleep 10
fi

# Wait for frontend
for i in {1..15}; do
    if lsof -i :3000 2>/dev/null | grep -q LISTEN; then
        break
    fi
    sleep 1
done

# Open browser
open "http://localhost:3000"

# Notification
osascript -e 'display notification "Quant Trading System is running at http://localhost:3000" with title "Quant System"'
echo "Done! System should be running."