#!/usr/bin/env osascript

-- Quant Trading System Launcher for Mac
-- Double-click this app to start the system UI

set scriptPath to POSIX path of (path to me)
set quantDir to do shell script "dirname " & quoted form of scriptPath

-- Check if API server is running
set apiRunning to do shell script "lsof -i :5000 2>/dev/null | grep LISTEN | wc -l"

if apiRunning is "0" then
    -- Start API server
    do shell script "cd " & quoted form of quantDir & " && python3 api_server.py &"
    delay 2
end if

-- Check if frontend is running
set frontendRunning to do shell script "lsof -i :3000 2>/dev/null | grep LISTEN | wc -l"

if frontendRunning is "0" then
    -- Start frontend
    do shell script "cd " & quoted form of quantDir & "/frontend && nohup npm start > /tmp/frontend.log 2>&1 &"
    delay 10
end if

-- Open browser
open location "http://localhost:3000"

-- Show notification
display notification "Quant Trading System is running!" with title "Quant System"
