[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$MainEnv = Join-Path $Root ".env"
$FinanceEnv = Join-Path $Root ".env.worker.finance"
$ProjectsEnv = Join-Path $Root ".env.worker.projects"
$WorkerScript = Join-Path $Root "bot_worker_service.py"
$MainScript = Join-Path $Root "bot.py"

function Get-DotEnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path $Path)) { return $null }
    $Line = Get-Content $Path | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } | Select-Object -Last 1
    if (-not $Line) { return $null }
    $Value = ($Line -replace "^[^=]*=", "").Trim()
    return $Value.Trim('"').Trim("'")
}

function Is-Enabled([string]$Name) {
    return (Get-DotEnvValue $MainEnv $Name) -match "^(?i:true|1|yes|on)$"
}

if (-not (Test-Path $Python)) {
    throw "Project Python was not found: $Python. Create .venv and install requirements.txt first."
}
if (-not (Test-Path $MainEnv)) { throw "Main bot .env file was not found." }
if (-not (Test-Path $MainScript)) { throw "bot.py was not found." }

$Workers = @()
if (Is-Enabled "FINANCE_QUEUE_ENABLED") {
    if (-not (Test-Path $FinanceEnv)) {
        throw "FINANCE_QUEUE_ENABLED=true, but .env.worker.finance is missing. Create the private finance worker env file; never commit secrets."
    }
    $Workers += [pscustomobject]@{ Id = "finance"; Env = $FinanceEnv }
}
if (Is-Enabled "PROJECTS_QUEUE_ENABLED") {
    if (-not (Test-Path $ProjectsEnv)) {
        throw "PROJECTS_QUEUE_ENABLED=true, but .env.worker.projects is missing. Create the private projects worker env file; never commit secrets."
    }
    $Workers += [pscustomobject]@{ Id = "projects"; Env = $ProjectsEnv }
}
if (-not (Test-Path $WorkerScript)) { throw "bot_worker_service.py was not found." }

Write-Host "Starting the main bot and $($Workers.Count) worker process(es)..." -ForegroundColor Cyan
Start-Process -FilePath $Python -ArgumentList @("bot.py") -WorkingDirectory $Root
foreach ($Worker in $Workers) {
    $EnvPath = $Worker.Env.Replace("'", "''")
    $Command = "`$env:BOT_WORKER_ENV_FILE='$EnvPath'; & '$Python' '$WorkerScript'"
    Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $Command) -WorkingDirectory $Root
    Write-Host "Started worker: $($Worker.Id)" -ForegroundColor Green
}
if ($Workers.Count -eq 0) {
    Write-Warning "No workers are enabled in .env. The main bot was started separately."
}
Write-Host "Processes are starting in separate windows. Closing a window stops its process." -ForegroundColor Green
