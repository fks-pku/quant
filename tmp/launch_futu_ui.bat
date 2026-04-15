@echo off
:: launch_futu_ui.bat - Launch Futu/Moomoo trading platform UI
:: Usage: launch_futu_ui.bat [hk|us]
::   hk - Launch Futu (Hong Kong market)
::   us  - Launch Moomoo (US market)
::   If no argument, defaults to hk

setlocal enabledelayedexpansion

set "REGION=%~1"
if "%REGION%"=="" set "REGION=hk"

echo ========================================
echo Futu/Moomoo Trading Platform Launcher
echo ========================================
echo.

if "%REGION%"=="us" (
    echo Launching Moomoo (US market)...
    start "" "Moomoo" 2>nul
    if errorlevel 1 (
        start "" "Moomoo US" 2>nul
        if errorlevel 1 (
            echo ERROR: Moomoo not found.
            echo Please download from: https://www.moomoo.com
            exit /b 1
        )
    )
    echo Moomoo launched successfully.
) else (
    echo Launching Futu (HK market)...
    start "" "Futu" 2>nul
    if errorlevel 1 (
        start "" "Futu OpenAPI" 2>nul
        if errorlevel 1 (
            start "" "富途牛牛" 2>nul
            if errorlevel 1 (
                echo ERROR: Futu not found.
                echo Please download from: https://www.futunn.com
                exit /b 1
            )
        )
    )
    echo Futu launched successfully.
)

echo.
echo Done. The trading platform should now be open.
echo ========================================
endlocal
