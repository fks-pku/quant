param(
    [switch]$DryRun,
    [switch]$Force,
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

$PaperDayDir = Join-Path $RepoRoot "quant\infrastructure\var\paper_trading\$ExecutionDate"
$MarkerPath = Join-Path $PaperDayDir "_daily_replay_complete.json"
$LockPath = Join-Path $PaperDayDir "_daily_replay.lock"
if ((Test-Path $MarkerPath) -and -not $Force) {
    Write-Log "paper daily already complete for execution_date=$ExecutionDate marker=$MarkerPath"
    exit 0
}
New-Item -ItemType Directory -Force -Path $PaperDayDir | Out-Null
try {
    New-Item -ItemType File -Path $LockPath -ErrorAction Stop | Out-Null
} catch {
    Write-Log "paper daily lock exists for execution_date=$ExecutionDate lock=$LockPath"
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

Write-Log "paper daily start signal_date=$SignalDate execution_date=$ExecutionDate dry_run=$DryRun"
Write-Log "command: $Python $($Args -join ' ')"

try {
    if ($DryRun) {
        Write-Log "dry run complete"
        exit 0
    }

    $ExitCode = Invoke-LoggedNative -FilePath $Python -Arguments $Args
    if ($ExitCode -eq 0) {
        $Marker = @{
            source = "run_paper_daily"
            signal_date = $SignalDate
            execution_date = $ExecutionDate
            updated_at = (Get-Date).ToString("s")
        } | ConvertTo-Json
        $Marker | Set-Content -Encoding UTF8 -Path $MarkerPath
    }
    Write-Log "paper daily exit_code=$ExitCode"
    exit $ExitCode
} finally {
    Remove-Item -Force $LockPath -ErrorAction SilentlyContinue
}
