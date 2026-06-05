param(
    [switch]$DryRun,
    [switch]$ConfirmRealOrders,
    [switch]$PendingOnly,
    [switch]$SkipPaper
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$Python = Join-Path $RepoRoot ".venv-qmt\Scripts\python.exe"
$ConfigDir = Join-Path $RepoRoot "quant\infrastructure\var\qmt_live_config"
$DuckdbDir = Join-Path $RepoRoot "quant\infrastructure\var\duckdb\live"
$DbPath = Join-Path $DuckdbDir "cn_ohlcv.duckdb"
$DateResolver = Join-Path $RepoRoot "quant\scripts\resolve_cn_trading_date.py"
$LogDir = Join-Path $RepoRoot "logs\scheduled"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Today = Get-Date
$Stamp = $Today.ToString("yyyyMMdd")
$LogPrefix = if ($DryRun) { "dryrun_qmt_live_daily" } else { "qmt_live_daily" }
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

$SignalDate = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "latest-data").Trim()
if ($LASTEXITCODE -ne 0) {
    Write-Log "failed to resolve signal date exit_code=$LASTEXITCODE"
    exit $LASTEXITCODE
}
if (-not $SignalDate) {
    Write-Log "failed to resolve signal date"
    exit 1
}
$ExecutionDate = $Today.ToString("yyyy-MM-dd")
if ($PendingOnly) {
    $ExecutionDate = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "next" "--date" $SignalDate).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Log "failed to resolve next trading date for signal_date=$SignalDate exit_code=$LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

$Args = @(
    (Join-Path $RepoRoot "quant\quant_system.py"),
    "--config", $ConfigDir,
    "--mode", "live",
    "--simulate-daily",
    "--signal-date", $SignalDate,
    "--execution-date", $ExecutionDate,
    "--snapshot-provider", "duckdb"
)
if ($PendingOnly) {
    $Args += "--pending-only"
}

Write-Log "qmt live daily start signal_date=$SignalDate execution_date=$ExecutionDate dry_run=$DryRun confirm_real_orders=$ConfirmRealOrders pending_only=$PendingOnly"
Write-Log "command: $Python $($Args -join ' ')"
Write-Log "post-close paper replay enabled=$(-not $SkipPaper)"

if ($DryRun) {
    if ($PendingOnly) {
        Write-Log "pending-only dry run would record next-session signals without broker submission"
    }
    if (-not $SkipPaper) {
        Write-Log "paper replay is deferred until the post-close data update supplies execution-date bars"
    }
    Write-Log "dry run complete"
    exit 0
}

if (-not $PendingOnly -and -not $ConfirmRealOrders) {
    Write-Log "refuse real-order run without -ConfirmRealOrders"
    exit 2
}
if ($PendingOnly) {
    Write-Log "pending-only mode: broker submission disabled and -ConfirmRealOrders is not required"
}

$LiveExit = Invoke-LoggedNative -FilePath $Python -Arguments $Args
$PaperExit = 0
if (-not $SkipPaper) {
    Write-Log "paper replay deferred to post-close data update for signal_date=$SignalDate execution_date=$ExecutionDate"
}
Write-Log "qmt live daily exit_code=$LiveExit paper_exit_code=$PaperExit"
if ($LiveExit -ne 0) {
    exit $LiveExit
}
exit $PaperExit
