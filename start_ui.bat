@echo off
cd /d "%~dp0"

echo ========================================
echo   Starting Quant Trading System UI...
echo ========================================

echo.
echo Starting API server on http://localhost:5000
start "Quant API Server" python api_server.py

ping -n 3 127.0.0.1 >nul

echo.
echo Opening browser...
start http://localhost:5000

echo.
echo ========================================
echo   Quant System UI is running!
echo   http://localhost:5000
echo   Close the API Server window to stop
echo ========================================
