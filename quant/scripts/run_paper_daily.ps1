param(
    [switch]$DryRun,
    [switch]$Force,
    [switch]$SignalOnly,
    [string]$SignalDate,
    [string]$ExecutionDate
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$ConfigDir = Join-Path $RepoRoot "quant\infrastructure\var\paper_config"
$DuckdbDir = Join-Path $RepoRoot "quant\infrastructure\var\duckdb\live"
$DbPath = Join-Path $DuckdbDir "cn_ohlcv.duckdb"
$DateResolver = Join-Path $RepoRoot "quant\scripts\resolve_cn_trading_date.py"
$LogDir = Join-Path $RepoRoot "logs\scheduled"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Today = Get-Date
$Stamp = $Today.ToString("yyyyMMdd_HHmmss")
$LogPrefix = if ($DryRun) { "dryrun_paper_daily" } else { "paper_daily" }
$LogPath = Join-Path $LogDir "$LogPrefix`_$Stamp.log"

function Write-Log {
    param([string]$Message)
    $Line = "{0} {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    $Line | Tee-Object -FilePath $LogPath -Append
}

function Invoke-LoggedNative {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    $RunId = [Guid]::NewGuid().ToString("N")
    $StdoutPath = Join-Path $LogDir "native_stdout_$RunId.log"
    $StderrPath = Join-Path $LogDir "native_stderr_$RunId.log"
    $Process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $RepoRoot `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath
    foreach ($Path in @($StdoutPath, $StderrPath)) {
        if (Test-Path $Path) {
            Get-Content $Path |
                Tee-Object -FilePath $LogPath -Append |
                ForEach-Object { Write-Host $_ }
            Remove-Item -Force $Path
        }
    }
    return $Process.ExitCode
}

if (-not (Test-Path $Python)) {
    Write-Log "missing python: $Python"
    exit 1
}
if (-not (Test-Path $ConfigDir)) {
    Write-Log "missing config dir: $ConfigDir"
    exit 1
}
if (-not (Test-Path $DbPath)) {
    Write-Log "missing DuckDB: $DbPath"
    exit 1
}
if (-not (Test-Path $DateResolver)) {
    Write-Log "missing date resolver: $DateResolver"
    exit 1
}

if (-not $Force) {
    $TodayText = $Today.ToString("yyyy-MM-dd")
    $IsOpen = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "is-open" "--date" $TodayText).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Log "failed to resolve trading calendar for target_date=$TodayText exit_code=$LASTEXITCODE"
        exit $LASTEXITCODE
    }
    if ($IsOpen -ne "1") {
        Write-Log "skip non-trading day: $TodayText"
        exit 0
    }
}

if ($SignalOnly) {
    if (-not $SignalDate) {
        $SignalDate = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "latest-data").Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Log "failed to resolve latest paper signal date exit_code=$LASTEXITCODE"
            exit $LASTEXITCODE
        }
    }
    if (-not $ExecutionDate) {
        $ExecutionDate = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "next" "--date" $SignalDate).Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Log "failed to resolve next paper execution date for signal_date=$SignalDate exit_code=$LASTEXITCODE"
            exit $LASTEXITCODE
        }
    }
} else {
    if (-not $SignalDate -or -not $ExecutionDate) {
        $ResolvedDates = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "latest-two-data").Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Log "failed to resolve paper dates exit_code=$LASTEXITCODE"
            exit $LASTEXITCODE
        }
        if (-not $ResolvedDates -or -not $ResolvedDates.Contains(",")) {
            Write-Log "failed to resolve paper dates"
            exit 1
        }
        $Parts = $ResolvedDates.Split(",")
        if (-not $SignalDate) {
            $SignalDate = $Parts[0]
        }
        if (-not $ExecutionDate) {
            $ExecutionDate = $Parts[1]
        }
    }
}

$DashboardDbPath = Join-Path $RepoRoot "quant\infrastructure\var\strategy_dashboard.duckdb"
if ((Test-Path $DashboardDbPath) -and -not $Force) {
    $env:CODEX_PAPER_DASHBOARD_DB = $DashboardDbPath
    $env:CODEX_PAPER_COMPLETION_DATE = $(if ($SignalOnly) { $SignalDate } else { $ExecutionDate })
    $env:CODEX_PAPER_SIGNAL_DATE = $SignalDate
    $env:CODEX_PAPER_COMPLETION_MODE = $(if ($SignalOnly) { "signal" } else { "execution" })
    $CompletionCode = @"
import os
from pathlib import Path
from quant.infrastructure.execution.strategy_state_store import StrategyStateStore
store = StrategyStateStore(Path(os.environ["CODEX_PAPER_DASHBOARD_DB"]))
target = os.environ["CODEX_PAPER_COMPLETION_DATE"][:10]
signal_date = os.environ["CODEX_PAPER_SIGNAL_DATE"][:10]
mode = os.environ["CODEX_PAPER_COMPLETION_MODE"]
signals = store.get_recent_signals(mode="paper", days=30)
snapshots = store.get_all_snapshots_for_mode(mode="paper", limit=365)
if mode == "signal":
    done = any(str(row.get("signal_date", ""))[:10] == target for row in signals) or any(str(row.get("snapshot_date", ""))[:10] == target for row in snapshots)
else:
    due = [
        row for row in signals
        if str(row.get("signal_date", ""))[:10] == signal_date
        and str(row.get("submit_date", ""))[:10] == target
        and str(row.get("status", "")).lower() in {"accepted", "pending", "queued", "pending_submit"}
    ]
    terminal = {"filled", "cancelled", "canceled", "rejected", "failed"}
    execution_rows = [
        row for row in signals
        if str(row.get("record_date") or row.get("signal_date") or "")[:10] == target
        and (
            float(row.get("fill_quantity") or 0.0) > 0
            or str(row.get("status", "")).lower() in terminal
        )
    ]
    def matched(signal):
        key = (
            str(signal.get("strategy_name") or ""),
            str(signal.get("symbol") or "").split(".")[0],
            str(signal.get("side") or "").upper(),
        )
        qty = float(signal.get("quantity") or 0.0)
        for row in execution_rows:
            row_key = (
                str(row.get("strategy_name") or ""),
                str(row.get("symbol") or "").split(".")[0],
                str(row.get("side") or "").upper(),
            )
            if row_key == key and abs(float(row.get("quantity") or 0.0) - qty) <= 1e-9:
                return True
        return False
    done = all(matched(row) for row in due) if due else any(str(row.get("snapshot_date", ""))[:10] == target for row in snapshots)
print("1" if done else "0")
"@
    $AlreadyComplete = (& $Python "-c" $CompletionCode).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Log "failed to check paper completion in DuckDB exit_code=$LASTEXITCODE"
        exit $LASTEXITCODE
    }
    if ($AlreadyComplete -eq "1") {
        Write-Log "paper daily already complete for signal_date=$SignalDate execution_date=$ExecutionDate signal_only=$SignalOnly db=$DashboardDbPath"
        exit 0
    }
}
$LockDir = Join-Path $RepoRoot "quant\infrastructure\var\paper_trading\locks"
$LockName = if ($SignalOnly) { "$SignalDate.signal_only.lock" } else { "$ExecutionDate.daily_replay.lock" }
$LockPath = Join-Path $LockDir $LockName
New-Item -ItemType Directory -Force -Path $LockDir | Out-Null
try {
    New-Item -ItemType File -Path $LockPath -ErrorAction Stop | Out-Null
} catch {
    Write-Log "paper daily lock exists for signal_date=$SignalDate execution_date=$ExecutionDate signal_only=$SignalOnly lock=$LockPath"
    exit 0
}

$Args = @(
    (Join-Path $RepoRoot "quant\quant_system.py"),
    "--config", $ConfigDir,
    "--mode", "paper",
    "--simulate-daily",
    "--signal-date", $SignalDate,
    "--execution-date", $ExecutionDate,
    "--snapshot-provider", "duckdb"
)
if ($SignalOnly) {
    $Args += "--pending-only"
}

Write-Log "paper daily start signal_date=$SignalDate execution_date=$ExecutionDate signal_only=$SignalOnly dry_run=$DryRun"
Write-Log "command: $Python $($Args -join ' ')"

try {
    if ($DryRun) {
        Write-Log "dry run complete"
        exit 0
    }

    $ExitCode = Invoke-LoggedNative -FilePath $Python -Arguments $Args
    Write-Log "paper daily exit_code=$ExitCode"
    exit $ExitCode
} finally {
    Remove-Item -Force $LockPath -ErrorAction SilentlyContinue
}
