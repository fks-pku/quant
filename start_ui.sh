#!/bin/bash
# Start Quant Trading System UI

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Starting Quant Trading System UI..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Install dependencies if needed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required"
    exit 1
fi

# Install Flask if not present
if ! python3 -c "import flask" &> /dev/null; then
    echo "Installing Flask..."
    pip3 install flask flask-cors
fi

# Install frontend dependencies if needed
if [ ! -d "quant/frontend/node_modules" ]; then
    echo "Installing frontend dependencies..."
    cd quant/frontend && npm install && cd ../..
fi

# Start Flask API in background
echo ""
echo "Starting API server on http://localhost:5000"
python3 quant/api_server.py &
API_PID=$!

# Wait a moment for API to start
sleep 2

# Start React frontend
echo ""
echo "Starting React frontend on http://localhost:3000"
cd quant/frontend && BROWSER=none npm start &
FRONTEND_PID=$!

# Open browser
echo ""
echo "Opening browser..."
sleep 3
open http://localhost:3000

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Quant System UI is running!"
echo ""
echo "  API Server: http://localhost:5000"
echo "  Frontend:   http://localhost:3000"
echo ""
echo "  Press Ctrl+C to stop"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Wait for both processes
wait $API_PID $FRONTEND_PID
