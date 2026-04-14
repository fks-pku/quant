#!/usr/bin/env osascript

-- Quant Trading System Launcher for Mac
-- Double-click this app to start the system UI

set scriptPath to POSIX path of (path to me)
set quantDir to do shell script "dirname " & quoted form of scriptPath

-- Run the bash launcher script (handles service startup and browser opening)
do shell script quoted form of (quantDir & "/start_system.sh") & " &"