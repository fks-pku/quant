param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$Python = Join-Path $RepoRoot ".venv-qmt\Scripts\python.exe"
$ConfigDir = Join-Path $RepoRoot "quant\infrastructure\var\qmt_live_config"
$LogDir = Join-Path $RepoRoot "logs\scheduled"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Today = Get-Date
$Stamp = $Today.ToString("yyyyMMdd")
$LogPrefix = if ($DryRun) { "dryrun_qmt_live_recovery" } else { "qmt_live_recovery" }
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

$Args = @(
    (Join-Path $RepoRoot "quant\quant_system.py"),
    "--config", $ConfigDir,
    "--mode", "live",
    "--recover-trades-only"
)

Write-Log "qmt live recovery start read_only=true dry_run=$DryRun"
Write-Log "command: $Python $($Args -join ' ')"

if ($DryRun) {
    Write-Log "dry run complete"
    Write-Log "qmt live recovery exit_code=0"
    exit 0
}

$ExitCode = Invoke-LoggedNative -FilePath $Python -Arguments $Args
Write-Log "qmt live recovery exit_code=$ExitCode"
exit $ExitCode
