param(
    [switch]$DryRun,
    [switch]$SkipPaper
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DuckdbDir = Join-Path $RepoRoot "quant\infrastructure\var\duckdb\live"
$DateResolver = Join-Path $RepoRoot "quant\scripts\resolve_cn_trading_date.py"
$LogDir = Join-Path $RepoRoot "logs\scheduled"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Today = Get-Date
$TargetDate = $Today.ToString("yyyy-MM-dd")
$Stamp = $Today.ToString("yyyyMMdd")
$LogPrefix = if ($DryRun) { "dryrun_update_cn_data_oss" } else { "update_cn_data_oss" }
$PublishPrefix = if ($DryRun) { "dryrun_oss_snapshot" } else { "oss_snapshot" }
$LogPath = Join-Path $LogDir "$LogPrefix`_$Stamp.log"
$PublishLog = Join-Path $LogDir "$PublishPrefix`_$Stamp.log"

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
if (-not (Test-Path $DateResolver)) {
    Write-Log "missing date resolver: $DateResolver"
    exit 1
}

$IsOpen = (& $Python $DateResolver "--duckdb-dir" $DuckdbDir "is-open" "--date" $TargetDate).Trim()
if ($LASTEXITCODE -ne 0) {
    Write-Log "failed to resolve trading calendar for target_date=$TargetDate exit_code=$LASTEXITCODE"
    exit $LASTEXITCODE
}
if ($IsOpen -ne "1") {
    Write-Log "skip non-trading day: $TargetDate"
    exit 0
}

$UpdateArgs = @(
    (Join-Path $RepoRoot "quant\scripts\update_cn_live_data.py"),
    "--end", $TargetDate,
    "--overlap-days", "3"
)
$PublishArgs = @(
    (Join-Path $RepoRoot "quant\scripts\publish_parquet_lake.py"),
    "snapshot",
    "--date", $TargetDate,
    "--log-file", $PublishLog
)

Write-Log "daily data update start target_date=$TargetDate dry_run=$DryRun"
Write-Log "update command: $Python $($UpdateArgs -join ' ')"
Write-Log "publish command: $Python $($PublishArgs -join ' ')"
Write-Log "live recovery enabled=true"
Write-Log "paper replay enabled=$(-not $SkipPaper)"

if ($DryRun) {
    $CheckArgs = @(
        (Join-Path $RepoRoot "quant\scripts\publish_parquet_lake.py"),
        "snapshot",
        "--date", $TargetDate,
        "--check-only",
        "--log-file", $PublishLog
    )
    $CheckExit = Invoke-LoggedNative -FilePath $Python -Arguments $CheckArgs
    Write-Log "dry run check exit_code=$CheckExit"
    exit $CheckExit
}

$UpdateExit = Invoke-LoggedNative -FilePath $Python -Arguments $UpdateArgs
if ($UpdateExit -ne 0) {
    Write-Log "update exit_code=$UpdateExit"
    exit $UpdateExit
}

$LiveRecoveryExit = 0
$LiveRecoveryScript = Join-Path $RepoRoot "quant\scripts\run_qmt_live_recovery.ps1"
if (-not (Test-Path $LiveRecoveryScript)) {
    Write-Log "missing live recovery script: $LiveRecoveryScript"
    $LiveRecoveryExit = 1
} else {
    $LiveRecoveryArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $LiveRecoveryScript
    )
    Write-Log "live recovery command: powershell.exe $($LiveRecoveryArgs -join ' ')"
    $LiveRecoveryExit = Invoke-LoggedNative -FilePath "powershell.exe" -Arguments $LiveRecoveryArgs
    Write-Log "live recovery exit_code=$LiveRecoveryExit"
}

$LivePendingExit = 0
$LivePendingScript = Join-Path $RepoRoot "quant\scripts\run_qmt_live_daily.ps1"
if ($LiveRecoveryExit -ne 0) {
    Write-Log "skip live pending because live recovery failed exit_code=$LiveRecoveryExit"
    $LivePendingExit = $LiveRecoveryExit
} elseif (-not (Test-Path $LivePendingScript)) {
    Write-Log "missing live pending script: $LivePendingScript"
    $LivePendingExit = 1
} else {
    $LivePendingArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $LivePendingScript,
        "-PendingOnly",
        "-SkipPaper"
    )
    Write-Log "live pending command: powershell.exe $($LivePendingArgs -join ' ')"
    $LivePendingExit = Invoke-LoggedNative -FilePath "powershell.exe" -Arguments $LivePendingArgs
    Write-Log "live pending exit_code=$LivePendingExit"
}

$PaperExit = 0
if (-not $SkipPaper) {
    $PaperScript = Join-Path $RepoRoot "quant\scripts\run_paper_daily.ps1"
    if (-not (Test-Path $PaperScript)) {
        Write-Log "missing paper replay script: $PaperScript"
        $PaperExit = 1
    } else {
        $PaperArgs = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $PaperScript
        )
        Write-Log "paper replay command: powershell.exe $($PaperArgs -join ' ')"
        $PaperExit = Invoke-LoggedNative -FilePath "powershell.exe" -Arguments $PaperArgs
        Write-Log "paper replay exit_code=$PaperExit"
    }
}

$PublishExit = Invoke-LoggedNative -FilePath $Python -Arguments $PublishArgs
Write-Log "publish exit_code=$PublishExit"
if ($PublishExit -ne 0) {
    exit $PublishExit
}
if ($LiveRecoveryExit -ne 0) {
    exit $LiveRecoveryExit
}
if ($LivePendingExit -ne 0) {
    exit $LivePendingExit
}
exit $PaperExit
