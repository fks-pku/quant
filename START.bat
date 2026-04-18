@echo off
cd /d "%~dp0"

echo Starting Quant Trading System...
echo.

REM Kill any existing processes on relevant ports
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do taskkill //F //PID %%P 2>nul
for /f "tokens=5" %%P in ('netstat -ano ^| findstr :3000 ^| findstr LISTENING') do taskkill //F //PID %%P 2>nul
timeout /t 2 /nobreak >nul

REM Start API server (serves built frontend on port 5000)
start "Quant System" cmd /c "cd /d "%~dp0" && python api_server.py"

REM Wait for server to start
echo Waiting for server...
timeout /t 4 /nobreak >nul

REM Open browser
start http://localhost:5000

echo.
echo System started at http://localhost:5000
echo.
echo Close this window to exit.
pause
