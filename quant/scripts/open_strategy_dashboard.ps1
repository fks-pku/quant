param(
    [int]$Port = 8791
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Url = "http://127.0.0.1:$Port/"
$HealthUrl = "${Url}api/health"
$DashboardUrl = "${Url}api/dashboard"
$LogDir = Join-Path $RepoRoot "logs\dashboard"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-DashboardReady {
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 1
        return $Response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-DashboardCompatible {
    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $DashboardUrl -TimeoutSec 2
        if ($Response.StatusCode -ne 200) {
            return $false
        }
        $Payload = $Response.Content | ConvertFrom-Json
        if ($null -eq $Payload.dashboard_asset_version) {
            return $false
        }
        foreach ($Strategy in @($Payload.strategies)) {
            if ($null -eq $Strategy.initial_cash) {
                return $false
            }
            if ($null -eq $Strategy.live -or $null -eq $Strategy.paper) {
                return $false
            }
            if ($null -eq $Strategy.live.initial_cash -or $null -eq $Strategy.paper.initial_cash) {
                return $false
            }
        }
        return $true
    } catch {
        return $false
    }
}

function Stop-DashboardOnPort {
    try {
        $Connection = Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $Connection -and $null -ne $Connection.OwningProcess) {
            Stop-Process -Id $Connection.OwningProcess -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        }
    } catch {
    }
}

function Start-DashboardServer {
    $Server = Join-Path $RepoRoot "quant\scripts\strategy_dashboard_server.py"
    $OutLog = Join-Path $LogDir "strategy_dashboard.out.log"
    $ErrLog = Join-Path $LogDir "strategy_dashboard.err.log"
    $Args = @($Server, "--host", "127.0.0.1", "--port", "$Port")
    Start-Process -WindowStyle Hidden -FilePath $Python -ArgumentList $Args -WorkingDirectory $RepoRoot -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog | Out-Null

    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Milliseconds 300
        if (Test-DashboardReady) {
            break
        }
    }
}

if ((Test-DashboardReady) -and -not (Test-DashboardCompatible)) {
    Stop-DashboardOnPort
}

if (-not (Test-DashboardReady)) {
    Start-DashboardServer
}

for ($i = 0; $i -lt 10; $i++) {
    if ((Test-DashboardReady) -and (Test-DashboardCompatible)) {
        break
    }
    Start-Sleep -Milliseconds 300
}

Start-Process $Url
