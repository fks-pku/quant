@echo off
cd /d "%~dp0"

echo Starting Quant Trading System...
echo.

REM Kill any existing processes
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM node.exe >nul 2>&1
timeout /t 1 /nobreak >nul

REM Start API server in background
start "QuantAPI" cmd /c "cd /d "%~dp0" && python api_server.py"

REM Wait for API
timeout /t 3 /nobreak >nul

REM Start frontend in background
cd frontend
start "QuantUI" cmd /c "npm start"
cd ..

REM Wait for frontend to compile
timeout /t 12 /nobreak >nul

REM Open browser
start http://localhost:3000

echo System is starting...
echo.
echo Wait 15 seconds for everything to load.
echo If browser doesn't open, go to: http://localhost:3000
echo.
echo Close this window to exit.
pause
