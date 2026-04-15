#!/bin/bash
# launch_futu_ui.sh - Launch Futu/Moomoo trading platform UI
# Usage: ./launch_futu_ui.sh [hk|us]
#   hk - Launch Futu (Hong Kong market)
#   us  - Launch Moomoo (US market)
#   If no argument, defaults to hk

set -e

REGION="${1:-hk}"

echo "========================================"
echo "Futu/Moomoo Trading Platform Launcher"
echo "========================================"

case "$OSTYPE" in
  darwin*)
    echo "[macOS detected]"
    if [ "$REGION" = "us" ]; then
      echo "Launching Moomoo (US market)..."
      if open -a "Moomoo" 2>/dev/null; then
        echo "Moomoo launched successfully."
      elif open -a "Moomoo US" 2>/dev/null; then
        echo "Moomoo US launched successfully."
      else
        echo "ERROR: Moomoo not found."
        echo "Please download from: https://www.moomoo.com"
        exit 1
      fi
    else
      echo "Launching Futu (HK market)..."
      if open -a "Futu" 2>/dev/null; then
        echo "Futu launched successfully."
      elif open -a "富途牛牛" 2>/dev/null; then
        echo "Futu (Chinese name) launched successfully."
      else
        echo "ERROR: Futu not found."
        echo "Please download from: https://www.futunn.com"
        exit 1
      fi
    fi
    ;;
  msys*|mingw*|cygwin*)
    echo "[Windows detected]"
    if [ "$REGION" = "us" ]; then
      echo "Launching Moomoo (US market)..."
      if command -v start &> /dev/null; then
        start "" "Moomoo" 2>/dev/null || start "" "Moomoo US" 2>/dev/null || {
          echo "ERROR: Moomoo not found."
          echo "Please download from: https://www.moomoo.com"
          exit 1
        }
        echo "Moomoo launched successfully."
      fi
    else
      echo "Launching Futu (HK market)..."
      if command -v start &> /dev/null; then
        start "" "Futu" 2>/dev/null || start "" "富途牛牛" 2>/dev/null || {
          echo "ERROR: Futu not found."
          echo "Please download from: https://www.futunn.com"
          exit 1
        }
        echo "Futu launched successfully."
      fi
    fi
    ;;
  *)
    echo "ERROR: Unsupported operating system: $OSTYPE"
    echo "Supported: macOS (darwin), Windows (msys/mingw/cygwin)"
    exit 1
    ;;
esac

echo ""
echo "Done. The trading platform should now be open."
echo "========================================"
