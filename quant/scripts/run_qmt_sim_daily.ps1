param(
    [switch]$DryRun,
    [string]$SignalDate,
    [string]$ExecutionDate
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$LogDir = Join-Path $RepoRoot "logs\scheduled"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("qmt_sim_daily_deprecated_{0}.log" -f (Get-Date).ToString("yyyyMMdd"))

function Write-Log {
    param([string]$Message)
    $Line = "{0} {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    $Line | Tee-Object -FilePath $LogPath -Append
}

$PaperScript = Join-Path $ScriptDir "run_paper_daily.ps1"
Write-Log "run_qmt_sim_daily.ps1 is deprecated; delegating to run_paper_daily.ps1"

$Args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PaperScript)
if ($DryRun) {
    $Args += "-DryRun"
}
if ($SignalDate) {
    $Args += @("-SignalDate", $SignalDate)
}
if ($ExecutionDate) {
    $Args += @("-ExecutionDate", $ExecutionDate)
}

& powershell.exe @Args
exit $LASTEXITCODE
