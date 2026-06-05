@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0quant\scripts\open_strategy_dashboard.ps1" -Port 8791
